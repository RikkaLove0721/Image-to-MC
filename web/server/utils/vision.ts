import fs from 'node:fs'
import { chat, imagePart, parseJsonLoose } from './ai'
import { runCoreJson } from './core'

/**
 * 视觉层：让"眼睛"和"设计师"分工。
 *
 * 关于"能不能给主模型加视觉"：可以，而且默认就是这么走的。DeepSeek 的
 * `deepseek-chat` / `deepseek-flash` 现在都收 image_url，实测能读出图里的字；
 * 但同一端点的 `deepseek-v4-pro` 会把图片变成 `[Unsupported Image]`。
 * 也就是说能力**按模型分、且会随版本变**，所以这里不写死任何判断，
 * 由 `utils/visionProbe.ts` 用一张 K7Q 小图当场测，测完缓存。
 *
 * 这一层仍然只让视觉模型输出**结构化判读**（层数/开间/屋面/构件），
 * 几十行 JSON，主模型拿它当"照片的官方说明"来写蓝图。好处不是省模型，
 * 而是让"看图"和"设计"两件事各自可替换、可降级。
 *
 * 第二个用途是复核：把我们生成的模型渲染图和原图拼成一张，让视觉模型
 * 直接说"差在哪"。本地体检只能算比例和密度，算不出"屋顶看起来像块板"。
 */

const SUBJECTS = 'building（建筑）/ ship（船）/ vehicle（车）/ aircraft（飞机）/ object（其他物件）'

const READ_PROMPT = `你是一位建筑测绘师。看这张照片，只输出一个 JSON 对象，不要解释、不要代码块：
{
  "subject": "building | ship | vehicle | aircraft | object",
  "type": "尽量具体的类型，例如 法式宫殿 / 哥特教堂 / 三层联排 / 现代图书馆 / 驱逐舰",
  "floors": 2,
  "bays": 13,
  "symmetry": "对称 | 不对称",
  "massing": "一句话描述体块构成，例如 中央主体略高前凸 + 左右两翼，各段进深相同",
  "roof": {"type": "flat | gable | hip | mansard | none", "height_ratio": 0.3, "dormers": 6},
  "materials": {"wall": "白色石材", "trim": "浅灰石材", "roof": "深灰石板"},
  "ornaments": ["arcade", "portico", "pediment", "column", "balcony", "dormer"],
  "railing": {"has": true, "where": "栏杆在哪：甲板四周 / 上层建筑平台 / 阳台 / 桥面两侧"},
  "flagpole": {"has": false, "where": "旗杆在哪：舰尾 / 桅顶 / 门前广场 / 屋顶中央"},
  "ship": {"bow": "left | right | toward | away | none", "superstructure": "上层建筑在船的前/中/后段", "deck_items": ["主炮", "垂发", "桅杆", "烟囱"]},
  "commercial": {
    "is_commercial": true,
    "signage": {"has": true, "color": "红底黄字", "where": "左侧竖向招牌柱 / 顶部横招牌带"},
    "bands": [{"y": "从下往上第几层", "color": "木纹棕", "what": "招牌带 / 白檐口 / 深色勒脚"}],
    "glass_ratio": 0.6,
    "cut": {"left": true, "right": false}
  },
  "confidence": 0.85,
  "notes": "照片里容易被误读的地方，例如 前景有旗杆、天空占比大、透视强"
}
计数规则：
- floors = 立面有几层窗（含被门/幕墙占掉的那层），不确定就给 2~3；
- bays = 正面横向有几组窗（一个开间算一组），数不清就给 0；
- roof.height_ratio = 屋顶（从檐口到屋脊）占建筑总高的比例，平顶给 0.05；
- dormers = 屋面上能数出来的老虎窗个数，没有给 0；
- **railing / flagpole 一定要如实填**：栏杆看"细密的竖向线条"（甲板边缘、
  平台外沿、阳台护栏），旗杆看"一根竖杆 + 顶端的布"。有没有、在哪，
  这两条直接决定模型上要不要铺 fence 和旗杆，比材质描述有用得多；
- 主体是船时，ship.bow 填船头**朝画面哪一侧**（left/right 就是左右，
  toward 是正对着你，away 是背对你），superstructure 填上层建筑在船体的
  前段/中段/后段，deck_items 列甲板上认得出的设备；
- **commercial 只对店面/餐厅/商铺/展厅这类现代商业立面填**，其余给 null：
  bands 是判断"层次"的关键 —— 从下往上一条一条数（深色勒脚、玻璃、
  白檐口、木纹带、招牌带…），这是建模时最该照抄的结构；
  glass_ratio = 玻璃（含幕墙）占整个立面的高度比例，0~1；
  **cut = 画面左/右边缘的建筑是不是被画框切断了**（玻璃墙一直延伸到画面外、
  看不到建筑真正的转角 → true）。这条决定要不要把建筑补全成完整一栋，
  看仔细：如果边缘能看到完整的转角、花池、柱子收头，那就是没被裁；
- 主体不是建筑时（subject != building），roof.type 给 "none"，其余字段尽力而为。`

const DIFF_PROMPT = `左图是原照片，右图是按这张照片做出来的 Minecraft 模型渲染。
只输出一个 JSON 对象，不要解释：
{
  "score": 0-100,
  "verdict": "一句话结论",
  "issues": [
    {"issue": "具体差在哪（体量/比例/屋顶/开间/颜色/构件）", "fix": "改蓝图的哪个字段、改成什么"}
  ]
}
要求：
- 最多 4 条，按"一眼就能看出"的严重程度排序，只写**看得见**的差异；
- 不要评价建模精度、材质贴图、光影这类与方块模型无关的东西；
- 如果右图已经很像，issues 给空数组，score 给 85 以上；
- fix 必须能落到右图这个体素模型的参数上（体块宽高、屋顶形式与高度、
  窗列数、颜色），不要写"重新设计"这种没法执行的话。
- **照片是斜拍的，透视会把细长物体压短**：原图是船/车这类细长主体时，
  不要拿照片里的"长短比例"去要求模型 —— 那量的是投影，不是实长。
  这类照片只比对：船头/车头是否尖、上层建筑的位置与层次、甲板构件、
  栏杆与旗杆的有无。
- **等轴测渲染有几个固定假象，不要当成错误**：
  ① 平屋顶在图上就是一大块平板 + 一圈女儿墙，**不要读成"坡屋顶/大屋盖"**；
  ② 进深是斜着画的，看起来会比实际深，**不要凭观感判"进深太长"**；
  ③ 窗户、幕墙在斜角下会被压缩，窄窗看着像细缝。
  这三条只能通过读蓝图的 roof.type 和体块尺寸判断，不要靠看图猜。`

/** 视觉模型读图：返回结构化判读，失败返回 null（不阻塞主流程）。 */
export async function readPhoto(photoPath: string): Promise<any | null> {
  if (!fs.existsSync(photoPath)) return null
  try {
    const raw = await chat(
      [
        { role: 'system', content: '你只输出 JSON，不输出任何解释。' },
        {
          role: 'user',
          content: [
            { type: 'text', text: READ_PROMPT },
            imagePart(photoPath),
          ],
        },
      ],
      { temperature: 0.1, maxTokens: 900, vision: true, timeoutMs: 120_000 },
    )
    const spec = parseJsonLoose(raw)
    if (!spec || typeof spec !== 'object') return null
    // 兜底成统一形状，后面用起来不用到处判空
    return {
      subject: String(spec.subject || 'building'),
      type: String(spec.type || ''),
      floors: Number(spec.floors) || 0,
      bays: Number(spec.bays) || 0,
      symmetry: String(spec.symmetry || ''),
      massing: String(spec.massing || ''),
      roof: {
        type: String(spec?.roof?.type || ''),
        height_ratio: Number(spec?.roof?.height_ratio) || 0,
        dormers: Number(spec?.roof?.dormers) || 0,
      },
      materials: spec.materials || {},
      ornaments: Array.isArray(spec.ornaments) ? spec.ornaments.map(String).slice(0, 8) : [],
      railing: {
        has: !!(spec?.railing?.has),
        where: String(spec?.railing?.where || ''),
      },
      flagpole: {
        has: !!(spec?.flagpole?.has),
        where: String(spec?.flagpole?.where || ''),
      },
      ship: {
        bow: String(spec?.ship?.bow || ''),
        superstructure: String(spec?.ship?.superstructure || ''),
        deck_items: Array.isArray(spec?.ship?.deck_items)
          ? spec.ship.deck_items.map(String).slice(0, 8)
          : [],
      },
      commercial: spec?.commercial
        ? {
            is_commercial: !!spec.commercial.is_commercial,
            signage: {
              has: !!(spec?.commercial?.signage?.has),
              color: String(spec?.commercial?.signage?.color || ''),
              where: String(spec?.commercial?.signage?.where || ''),
            },
            bands: Array.isArray(spec?.commercial?.bands)
              ? spec.commercial.bands.slice(0, 8).map((b: any) => ({
                  y: String(b?.y ?? ''),
                  color: String(b?.color ?? ''),
                  what: String(b?.what ?? ''),
                }))
              : [],
            glass_ratio: Number(spec?.commercial?.glass_ratio) || 0,
            cut: {
              left: !!spec?.commercial?.cut?.left,
              right: !!spec?.commercial?.cut?.right,
            },
          }
        : null,
      confidence: Number(spec.confidence) || 0,
      notes: String(spec.notes || ''),
    }
  } catch {
    return null
  }
}

/**
 * 让视觉模型比对"原图 vs 模型渲染"。
 *
 * 先在本地把两张图拼成一张（Python 侧有 Pillow），再发一次请求 ——
 * 一次读图就能做对比，比发两张图省一半 token，也避免模型在两张图之间串。
 */
export async function diffRender(
  photoPath: string,
  renderPath: string,
  outJpg: string,
  facts = '',
): Promise<{ score: number; verdict: string; issues: { issue: string; fix: string }[] } | null> {
  if (!fs.existsSync(renderPath)) return null
  try {
    const cmp = runCoreJson<any>(
      ['--compare', photoPath, renderPath, '--img-out', outJpg],
      60_000,
    )
    const file = String(cmp?.path || outJpg)
    if (!fs.existsSync(file)) return null
    const raw = await chat(
      [
        { role: 'system', content: '你只输出 JSON，不输出任何解释。' },
        {
          role: 'user',
          content: [
            { type: 'text', text: DIFF_PROMPT + facts },
            imagePart(file),
          ],
        },
      ],
      { temperature: 0.2, maxTokens: 900, vision: true, timeoutMs: 150_000 },
    )
    const spec = parseJsonLoose(raw)
    if (!spec) return null
    const issues = (Array.isArray(spec.issues) ? spec.issues : [])
      .slice(0, 4)
      .map((i: any) => ({ issue: String(i?.issue || ''), fix: String(i?.fix || '') }))
      .filter((i: any) => i.issue)
    return {
      score: Math.max(0, Math.min(100, Math.round(Number(spec.score) || 0))),
      verdict: String(spec.verdict || ''),
      issues,
    }
  } catch {
    return null
  } finally {
    try {
      fs.unlinkSync(outJpg)
    } catch {
      /* 临时拼图删不掉不影响结果 */
    }
  }
}

/** 把视觉判读写进给 DeepSeek 的提示词。这是"两个模型协作"的接口。 */
export function readingNote(reading: any): string {
  if (!reading) return ''
  const r = reading
  const parts: string[] = []
  if (r.type) parts.push(`类型：${r.type}`)
  if (r.floors) parts.push(`层数：${r.floors}`)
  if (r.bays) parts.push(`正面开间：${r.bays}`)
  if (r.symmetry) parts.push(`对称性：${r.symmetry}`)
  if (r.massing) parts.push(`体块：${r.massing}`)
  if (r.roof?.type && r.roof.type !== 'none') {
    const pct = r.roof.height_ratio ? `，占立面 ${Math.round(r.roof.height_ratio * 100)}%` : ''
    const dm = r.roof.dormers ? `，老虎窗 ${r.roof.dormers} 个` : ''
    parts.push(`屋顶：${r.roof.type}${pct}${dm}`)
  }
  const mats = Object.entries(r.materials || {})
    .map(([k, v]) => `${k}=${v}`)
    .join('，')
  if (mats) parts.push(`材质观感：${mats}`)
  if (r.ornaments?.length) parts.push(`构件：${r.ornaments.join('、')}`)
  // 商业立面的"层次"只能靠这一行传下去：本地像素分析数不出
  // "木纹带 2 格 / 白檐口 1 格"这种结构，而它恰恰是店面像不像的关键。
  const cm = r.commercial
  if (cm?.is_commercial) {
    if (cm.bands?.length) {
      parts.push(
        `立面水平分层（从下往上）：` +
          cm.bands
            .map((b: any) => [b.y, b.color, b.what].filter(Boolean).join(' '))
            .filter(Boolean)
            .join(' / '),
      )
    }
    if (cm.glass_ratio) parts.push(`玻璃占立面高度：${Math.round(cm.glass_ratio * 100)}%`)
    if (cm.signage?.has) {
      parts.push(
        `招牌：有${cm.signage.color ? `，${cm.signage.color}` : ''}${
          cm.signage.where ? `，位置「${cm.signage.where}」` : ''
        }`,
      )
    }
  }
  // 栏杆 / 旗杆是"非整方块"构件，本地分析完全看不见，只能靠这一行判读。
  if (r.railing?.has) {
    parts.push(`栏杆：有${r.railing.where ? `，位置「${r.railing.where}」` : '（位置未判读）'}`)
  }
  if (r.flagpole?.has) {
    parts.push(`旗杆：有${r.flagpole.where ? `，位置「${r.flagpole.where}」` : '（位置未判读）'}`)
  }
  if (r.ship?.bow && r.ship.bow !== 'none') {
    const dir: Record<string, string> = {
      left: '照片里船头朝画面左侧',
      right: '照片里船头朝画面右侧',
      toward: '船头正对镜头',
      away: '船头背对镜头',
    }
    parts.push(`船头朝向：${dir[r.ship.bow] || r.ship.bow}`)
    if (r.ship.superstructure) parts.push(`上层建筑：${r.ship.superstructure}`)
    if (r.ship.deck_items?.length) parts.push(`甲板设备：${r.ship.deck_items.join('、')}`)
  }
  if (r.notes) parts.push(`注意：${r.notes}`)

  const notBuilding =
    r.subject && r.subject !== 'building'
      ? `\n- **注意：视觉模型判断这张图的主体不是建筑（${r.subject}）**。` +
        `按住宅/宫殿那套语汇硬套会得到一个四不像。请按它的真实形态组织体块，` +
        `并把用户看的类型写进 building.type。`
      : ''
  return (
    `\n\n视觉模型对这张照片的判读（它真的看了图，比上面的数值更可靠，冲突时以它为准）：\n` +
    parts.map((p) => `- ${p}`).join('\n') +
    notBuilding
  )
}
