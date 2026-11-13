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

const KEEP_FIELDS = [
  'subject',
  'scale',
  'seed',
  'target_width',
  'target_height',
  'aspect',
  'detail',
  'ornament',
  'complete',
]

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
    '你只需要按他的意见改这一份蓝图，不要重新设计。\n' +
    '硬性要求：\n' +
    '1. 只改用户点到的地方；他没提的字段全部原样保留（体块坐标、层数、比例、seed 都不要动）。\n' +
    '2. 输出**完整的** JSON 对象（不是 diff、不是片段），字段结构与原蓝图完全一致。\n' +
    '3. 不要输出解释文字，不要用 markdown 代码块。\n' +
    '3b. **不要缩进、不要换行对齐**：紧凑的 JSON 一样合法，缩进会把输出撑到被截断。\n' +
    '4. 改动要落在蓝图的字段上：体量用 volumes、窗用 windows、构件用 details。\n' +
    '4b. **形体字段必须原样带过来**：volume 的 "shape"（hex/hex_front/oct/wedge/round/l/poly）、' +
    '"chamfer"、"lean"（{"x":…,"z":…}）、"skew" 是形体的一部分，重写时最容易丢。\n' +
    '    用户说"更斜一点"就是加大 lean 的格数；说"斜向另一边"就是改符号；' +
    '    说"一层不是方的"就是给底层加 shape；说"两个方向都斜"就是 x 和 z 都给。\n' +
    '5. 玻璃默认是玻璃板；整块玻璃要写 "thick": true（玻璃天花板/厚玻璃才用）。\n' +
    `6. 这些字段不要改：${KEEP_FIELDS.join(', ')}。`

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

  // 把"不该改"的字段钉回原值：模型经常顺手把 seed / 尺寸重写一遍
  for (const k of KEEP_FIELDS) {
    if (prev[k] !== undefined) next[k] = prev[k]
  }
  // 形体字段（shape / lean / skew）同样要钉：用户只让改颜色，
  // 结果斜的那一层变回方块 —— 这是"改完反而退步"的常见原因。
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
