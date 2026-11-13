import fs from 'node:fs'
import path from 'node:path'
import { checkModelName, projectDir, readProject, saveProject } from '../../../../utils/store'
import { chat, parseJsonLoose, type TokenUsage } from '../../../../utils/ai'
import { enforceShape, enforceTilt, pinShape } from '../../../../utils/geometry'
import { startJob } from '../../../../utils/jobs'

/**
 * 「修改」：拿着上一版的分析过程和蓝图，按用户的意见改一版新的。
 *
 * 和「重新生成」的区别：重生成是把照片重新读一遍，AI 可能把已经调好的
 * 比例、开间、配色全推翻；修改是**只动用户点到的部分**，其余字段原样保留。
 * 所以这里不重跑照片分析，只把 `<model>_ai.json`（分析快照）和
 * `<model>_spec.json`（蓝图）当上下文喂回去。
 *
 * 命名：`m09` → `m09_r2` → `m09_r3`，可以一直改下去。
 */

/** 第几次修改：从 r2 开始数（r1 就是原版） */
function nextRev(dir: string, base: string): { name: string; n: number } {
  let n = 2
  while (fs.existsSync(path.join(dir, 'output', `${base}_r${n}_manifest.json`))) n += 1
  return { name: `${base}_r${n}`, n }
}

export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id') || ''
  const project = readProject(id)
  const body = (await readBody(event)) || {}
  const base = checkModelName(body.model)
  const feedback = String(body.feedback || '').trim()
  if (!base) throw createError({ statusCode: 400, statusMessage: '缺少要修改的模型名' })
  if (!feedback) throw createError({ statusCode: 400, statusMessage: '说说哪里不满意，再点生成' })

  const dir = projectDir(id)
  const outDir = path.join(dir, 'output')
  const specFile = path.join(outDir, `${base}_spec.json`)
  const aiFile = path.join(outDir, `${base}_ai.json`)
  if (!fs.existsSync(specFile)) {
    throw createError({
      statusCode: 400,
      statusMessage: `「${base}」没有存蓝图，改不了 —— 只有 AI 方案生成的模型能这样迭代`,
    })
  }
  const prev = JSON.parse(fs.readFileSync(specFile, 'utf-8'))
  let analysis: any = null
  try {
    analysis = JSON.parse(fs.readFileSync(aiFile, 'utf-8'))
  } catch {
    /* 老模型没有分析快照，只靠蓝图也能改 */
  }

  // 分析记录压成几行字：完整 JSON 塞进去要几百 token，模型也不会因此更准
  const vision = analysis?.vision || {}
  const rounds = Array.isArray(analysis?.rounds) ? analysis.rounds : []
  const facts = [
    vision.type ? `照片判读：${vision.type}；体量 ${JSON.stringify(vision.massing || '')}` : '',
    vision.plan && !['rect', ''].includes(String(vision.plan))
      ? `一层平面：${vision.plan}${vision.plan_note ? `（${vision.plan_note}）` : ''} —— 要保留 shape 字段`
      : '',
    vision.slant && !/^(none|无|否|no|n\/a)$/i.test(String(vision.slant))
      ? `斜置：${vision.slant}${vision.slant_dir ? `（方向 ${vision.slant_dir}）` : ''} —— 要保留 lean 字段`
      : '',
    vision.bands?.length ? `立面分层（从下往上）：${vision.bands.map((b: any) => `${b.y}:${b.color || ''}${b.what || ''}`).join(' / ')}` : '',
    vision.signage?.has ? `招牌：${vision.signage.color || ''}${vision.signage.where || ''}` : '',
    vision.glass_ratio ? `玻璃占比：${vision.glass_ratio}` : '',
    analysis?.craft !== undefined ? `建筑技巧性档位：${analysis.craft}（0 低 / 1 中 / 2 高）` : '',
    analysis?.hint ? `用户上次的补充要求：${analysis.hint}` : '',
    rounds.length
      ? `上次的体检过程：${rounds
          .map(
            (r: any) =>
              `第${r.round}轮 ${r.score ?? '-'} 分` +
              (Array.isArray(r.issues) && r.issues.length ? `（${r.issues.slice(0, 4).join('；')}）` : ''),
          )
          .join(' → ')}`
      : '',
  ]
    .filter(Boolean)
    .join('\n')

  const system =
    '你是一个 Minecraft 建筑蓝图的"修改器"。用户对上一版结果不满意，' +
    '你按他的意见改这一份蓝图。\n' +
    '硬性要求：\n' +
    '1. **以用户的意见为准**。他要求改什么就改什么，包括比例、层数、体块数量、' +
    '   尺寸、细节等级、配色 —— 这些都可以动。没提到的部分尽量保留原样，' +
    '   但如果新的要求与之冲突（比如"太矮了要加两层"就该动层数），以要求为准。\n' +
    '2. 输出**完整的** JSON 对象（不是 diff、不是片段），字段结构与原蓝图完全一致。\n' +
    '3. 不要输出解释文字，不要用 markdown 代码块。\n' +
    '3b. **不要缩进、不要换行对齐**：紧凑的 JSON 一样合法，缩进会把输出撑到被截断。\n' +
    '4. 改动要落在蓝图的字段上：体量用 volumes、窗用 windows、构件用 details。\n' +
    '4b. **形体字段必须原样带过来**：volume 的 "shape"（hex/hex_front/oct/wedge/round/l/poly）、' +
    '"chamfer"、"lean"（{"x":…,"z":…}）、"skew" 是形体的一部分，重写时最容易丢。\n' +
    '    用户说"更斜一点"就是加大 lean 的格数；说"斜向另一边"就是改符号；' +
    '    说"一层不是方的"就是给底层加 shape；说"两个方向都斜"就是 x 和 z 都给。\n' +
    '5. 玻璃默认是玻璃板；整块玻璃要写 "thick": true（玻璃天花板/厚玻璃才用）。\n' +
    '6. `seed` 保留原值即可（它只影响观赏性细节的随机分布，不是设计参数）。'

  const user =
    (facts ? `【上一版的分析记录】\n${facts}\n\n` : '') +
    `【上一版蓝图】\n${JSON.stringify(prev, null, 1)}\n\n` +
    `【用户这次的修改意见】\n${feedback}\n\n` +
    '请输出修改后的完整 JSON。'

  let usage: TokenUsage = { prompt: 0, completion: 0, total: 0 }
  const raw = await chat(
    [
      { role: 'system', content: system },
      { role: 'user', content: user },
    ],
    {
      temperature: 0.25,
      // 蓝图本身 4~6 KB，模型还要照着重写一遍完整 JSON。
      // 8192 经常在最后一个 details 上被截断 —— 用户看到的就是"返回的蓝图出错"。
      maxTokens: 12000,
      effort: 'low',
      onUsage: (u) => {
        usage = u
      },
    },
  )
  const next = parseJsonLoose(raw)
  if (!next || typeof next !== 'object') {
    // 把原始返回落盘：不然用户只会看到一句"蓝图出错"，
    // 到底是截断、是拒绝回答、还是上游把 content 丢了，永远查不出来。
    try {
      fs.writeFileSync(path.join(outDir, `${base}_rev_fail.txt`), String(raw || '').slice(0, 20000), 'utf-8')
      console.error('[revise] 解析失败，原始返回已存 _rev_fail.txt，长度', String(raw || '').length)
    } catch {
      /* 落盘失败不影响报错 */
    }
    throw createError({
      statusCode: 502,
      statusMessage: `AI 没返回可用的蓝图（返回 ${String(raw || '').length} 字${
        String(raw || '').length > 3000 ? '，多半是被截断了' : ''
      }），原始内容存在 output/${base}_rev_fail.txt`,
    })
  }

  // seed 只影响观赏性细节的随机分布，不是设计参数：模型常常忘了带、
  // 或者顺手重写一遍，这里统一钉回原值，保证"同一版再改一次"不会
  // 因为细节重新洗牌而看不出改了哪儿。其余字段一律听 AI 的 ——
  // 用户要的是"完全自由的修改"，把比例/层数/尺寸钉死会让"太矮，加两层"
  // 这种要求直接失效。
  if (prev.seed !== undefined) next.seed = prev.seed
  // 室内设计同样是构建期开关：AI 重写蓝图时很容易把它漏掉，
  // 而用户根本没提这件事 —— 漏掉就等于"改一次颜色，屋里全空了"。
  if (prev.interior) next.interior = true
  else delete next.interior
  // 形体字段（shape / lean / skew）仍然兜一层：用户只让改颜色，
  // 结果斜的那一层变回方块 —— 这是"改完反而退步"的常见原因。
  // 但它只是"补空缺 + 校正方向"，不会把用户明确要求改的形体拉回去。
  const pinned = pinShape(next, prev)
  const enforced = [...enforceTilt(next, analysis?.vision), ...enforceShape(next, analysis?.vision)]
  if (pinned.length || enforced.length) {
    console.log('[revise] 形体兜底：', [...pinned, ...enforced].join('；'))
  }
  const rev = nextRev(dir, base)
  const modelName = rev.name
  const CN = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十']
  next.name = `${base} 第${CN[rev.n] || rev.n}次修改`
  const specPath = path.join(outDir, `${modelName}_spec.json`)
  fs.writeFileSync(specPath, JSON.stringify(next, null, 2), 'utf-8')

  const job = startJob(project, modelName, {
    spec: specPath,
    meta: {
      usage,
      // 分析过程继承上一版，再把这次的意见记上 —— 下一轮还能接着改
      analysis: {
        ...(analysis || {}),
        revisedFrom: base,
        feedback: [...(Array.isArray(analysis?.feedback) ? analysis.feedback : []), feedback],
        at: new Date().toISOString(),
      },
      parent: base,
      // 列表里显示成「m09_r2 · 第二次修改」，文件名保持 ASCII
      revLabel: `第${CN[rev.n] || rev.n}次修改`,
      craft: analysis?.craft ?? null,
    },
  })
  project.lastModel = modelName
  saveProject(project)
  return { job: { id: job.id, status: job.status, modelName }, usage }
})
