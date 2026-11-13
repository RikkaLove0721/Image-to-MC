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
  "massing": "一句话描述体块构成，例如 中央主体略高前凸 + 左右两翼 / 三个盒子上下错位、上层向左悬挑 3 格、底层内收成玻璃",
  "plan": "rect | hex | oct | round | wedge | L",
  "plan_note": "一层平面是什么形状，例如 六边形，正面窄、后面宽，两端各切掉一个角",
  "slant": "none",
  "slant_dir": "none | out | front | both | skew",
  "slant_kind": "none | band | soffit",
  "slant_tilt": "none | x+8 | x-5 | z+6",
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
- **type 里必须区分"现代/当代"与"古典/传统"**：玻璃幕墙、平屋顶、错位盒子、
  悬挑、竖向百叶 → 现代（科研楼/办公/美术馆/综合体）；孟莎顶、老虎窗、拱廊、
  柱廊 → 古典。这一条决定后面用哪套语汇建模，比材质描述重要；
- **massing 要写清"错位"**：哪一段在上、往哪个方向探出几格、底层有没有内收。
  错位悬挑的现代建筑被当成"主楼 + 两翼"来建，会变成一栋对称的府邸（实测过）；
- **slant 判"斜不斜"，这一条比材质重要得多**。做法：找最上面那个盒子的
  **最左边那条边**，从上往下看它 —— 是垂直的直线，还是斜线？
  再看这条边与底下那层的左墙线是否对齐。
  · 垂直、且与下层对齐 → 填 "none"；
  · 斜的、或明显比下层偏出去 → 写成一句话，必须带"哪一层 + 往哪边歪 + 几格"，
    例如 "上层左侧白盒的左边是一条斜线，整体向右歪约 5 格"、
    "中层长条盒子斜着摆，与底层不平行约 6 格"。
  · **不要因为"看起来像透视"就填 none**：正对着拍的房子，透视几乎为零，
    边线斜就是真的斜。只有明显的斜拍（能看到两个侧面、地面有纵深）才考虑透视。
  这条漏了，上层只能建成正交方块，用户会直接看出"和照片不是一回事"；
  把正交建筑硬编成斜的同样错，所以真的垂直就老实填 none；
- **slant_dir 填"往哪个方向斜"**，这一条决定模型里用哪个参数：
  · "out" = 只朝外挑（正面看，上层比下层偏出去，左右方向的斜）；
  · "front" = 只朝前（朝镜头/街道）挑 —— 看上层盒子的**底面与顶面**：
    顶面比底面更靠近镜头、下檐能看见天花板那一面，就是朝前挑；
  · "both" = 又朝外又朝前，**这是最常见的一种**：边线既斜、又看得见上层的底面；
    这种要同时写 lean 的 x 和 z，只写一个方向做出来还是"半个斜"；
  · "skew" = 平面斜着摆（俯看是平行四边形，立面本身垂直）；
  拿不准 out 还是 front 时，只要"边线斜"或"看得见底面"就填 "both"；
- **slant_kind 区分"怎么斜"，这一条决定模型是斜墙还是斜层**。
  只看一件事：那条斜的边，是**竖直的墙**在斜，还是**水平的面（楼板/底面）**在斜？
  · "band" = 中间有一整层是斜的：这一层的**底边和顶边都不水平**，
    像一条从左低走到右高的带子。照片里"整层楼歪着搭上去"就是这种 ——
    哪怕它的墙是垂直的，只要楼板斜了就要填 band；
  · "soffit" = 体块本身是方正的，只有**底面被斜切**（悬挑盒子底下那一刀，
    从外往里越切越高 / 从一头斜到另一头），顶面还是平的；
  · 看不清或者两层都正 → "none"。
  这一条填错，做出来的东西不是"斜墙"就是"陡坡"，用户一眼能看出来；
- **slant_tilt 写坡度本身，格式"轴 + 符号 + 格数"**，例如：
  · "x+8" = 左右方向，**右边比左边高 8 格**；
  · "x-6" = 左右方向，右边比左边低 6 格（左高右低）；
  · "z+5" = 前后方向，**近处（朝镜头/朝街那一侧）比远处高 5 格**；
  · "z-5" = 近处更低。
  格数按整个体块的长度估：一条 40 格长的带子斜 8~10 格才看得出来，
  只斜 2~3 格在方块模型里等于没斜 —— 宁可写大一点，也不要写 1~2。
  真的不斜就填 "none"，不要为了"显得不规则"编一个坡度出来；
- **plan 填一层平面的轮廓形状**：正对着看不到地面时，看**两侧墙是不是斜着收进去**、
  转角是不是切掉一块。正面窄、两侧墙向外张开 → "hex"；
  **只有转角被斜切、前后墙仍是长直边，也算 "hex"**（这是建筑里最常见的那种）；
  四角都切 → "oct"；一端收成尖 → "wedge"；弧形墙 → "round"；
  明显缺一角 → "L"；看不出 → "rect"。这一条决定要不要用多边形平面，
  填错会得到一栋方盒子；
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
- **专门查两条：斜置 + 平面轮廓**。
  ① 斜置：对比两图上层盒子的边线走向 —— 左图（原图）的边线是否与地面不平行、
  是否比底层偏出去？**再看它是不是也朝镜头方向探出来**（原图里能看见上层盒子的
  底面/下檐）。右图（模型）是不是一摞正交方块？只要原图斜、模型正，就必须列成
  一条 issue，fix 写成 "给第 N 个体块加 lean: {x: 格数, z: 格数}"——
  **两个方向都斜时 x 和 z 都要写**，只写一个还是"半个斜"。
  **更要紧的是分清"墙斜"还是"层斜"**：左图里如果中间那一层的**底边和顶边
  都不水平**（一条左低右高的带子），模型只把墙做成斜的、每层楼板仍是水平的，
  那就不对 —— 这条 issue 的 fix 要写 "给第 N 个体块加 tilt:
  {axis: \"x\", bottom: 格数, top: 格数}"（整层斜带），或者只写 bottom（斜底悬挑）。
  ② 平面轮廓：原图一层的两侧墙是不是斜着收进去、转角是不是被切掉一块
  （六边形楼层）？右图是不是一个方盒？是就列一条，fix 写成
  "把第 N 个体块改成 shape: hex / hex_front / oct"。
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
      // effort 必须是 low：视觉模型也是推理模型，思维链算在 max_tokens 里。
      // 用 900 token + 默认 high，思考就把预算烧完了，JSON 被截到一半 ——
      // 表现出来是"判读类型写成 flat、层数 0"，然后整条链路按错的类型写蓝图。
      // 2200 是够写 JSON 的，但**不够先想再写**：deepseek-flash 是推理模型，
      // 思考也算 completion。哥特教堂那种图一思考就把预算烧完，content 变成空，
      // chat() 回落到 reasoning_content —— 拿到的是一段"我们要输出 JSON…"的独白，
      // parseJsonLoose 解析失败、readPhoto 返回 null，整条链路就按"没有视觉"跑。
      // 实测 2200 在复杂立面上失败率过半，4200 才稳。
      { temperature: 0.1, maxTokens: 4200, effort: 'low', vision: true, timeoutMs: 150_000 },
    )
    const spec = parseJsonLoose(raw)
    if (!spec || typeof spec !== 'object') {
      console.error('[vision] 判读不是合法 JSON：', String(raw).slice(0, 200))
      return null
    }
    // 兜底成统一形状，后面用起来不用到处判空
    return {
      subject: String(spec.subject || 'building'),
      type: String(spec.type || ''),
      floors: Number(spec.floors) || 0,
      bays: Number(spec.bays) || 0,
      symmetry: String(spec.symmetry || ''),
      massing: String(spec.massing || ''),
      // 平面轮廓：六边形楼层 / 八边形塔楼 / 楔形 / 圆塔 —— 只靠 w/d 是写不出来的
      plan: String(spec.plan || 'rect').toLowerCase(),
      plan_note: String(spec.plan_note || ''),
      slant_dir: String(spec.slant_dir || '').toLowerCase(),
      // 斜的程度：band（整层斜带）/ soffit（悬挑盒的斜底），以及坡度 "x+8"
      slant_kind: String(spec.slant_kind || '').toLowerCase(),
      slant_tilt: String(spec.slant_tilt || '').toLowerCase(),
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
  } catch (e: any) {
    // 静默返回 null 会让界面看起来像"没开视觉"，真错因（图太大 / 401 /
    // 上游把图片丢了）必须落到服务端日志里，否则永远查不出来。
    console.error('[vision] 读图失败：', String(e?.statusMessage || e?.message || e).slice(0, 300))
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
      { temperature: 0.2, maxTokens: 3600, effort: 'low', vision: true, timeoutMs: 180_000 },
    )
    const spec = parseJsonLoose(raw)
    if (!spec) {
      console.error('[vision] 复核不是合法 JSON：', String(raw).slice(0, 200))
      return null
    }
    const issues = (Array.isArray(spec.issues) ? spec.issues : [])
      .slice(0, 4)
      .map((i: any) => ({ issue: String(i?.issue || ''), fix: String(i?.fix || '') }))
      .filter((i: any) => i.issue)
    return {
      score: Math.max(0, Math.min(100, Math.round(Number(spec.score) || 0))),
      verdict: String(spec.verdict || ''),
      issues,
    }
  } catch (e: any) {
    // 复核失败等于这一轮"没看图"，分数会被压到量测的一半；把错因写进日志，
    // 不然只看到"分数莫名其妙掉了一半"。
    console.error('[vision] 复核失败：', String(e?.statusMessage || e?.message || e).slice(0, 300))
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
  // 斜置是最容易在"AI 写蓝图"这一步丢掉的信息：它不在比例/开间里，
  // 只在这句话里。丢掉之后上层只会被写成正交方块，用户一眼看出不像。
  const slant = typeof r.slant === 'string' ? r.slant.trim() : ''
  if (slant && !/^(none|无|否|no|n\/a)$/i.test(slant)) {
    parts.push(`斜置体块：有 —— ${slant}（必须用 volume 的 lean / skew 表达，不要写成正交盒子）`)
  }
  // 方向单独给一行：只写一股斜，模型做出来只有半个斜，正脸看着还是方块。
  const sdir = String(r.slant_dir || '').toLowerCase()
  const dirText: Record<string, string> = {
    out: '只朝外挑（lean 的 x）',
    front: '只朝前挑（lean 的 z）',
    both: '**同时朝外和朝前挑** —— 写 "lean": {"x": 4~6, "z": 3~5}，两个方向都要给',
    skew: '平面斜摆（skew）',
  }
  if (sdir && sdir !== 'none' && dirText[sdir]) {
    parts.push(`斜的方向：${dirText[sdir]}`)
  }
  // 斜的"种类"直接决定写哪个字段：斜墙写 lean/skew，斜层写 tilt。
  // 只给方向不给种类，AI 一律写 lean —— 于是二层还是水平的，只是墙歪了。
  const skind = String(r.slant_kind || '').toLowerCase()
  const tilt = String(r.slant_tilt || '').toLowerCase()
  if (skind === 'band') {
    parts.push(
      `斜的形态：**整层斜带**（楼板斜着走，这一层的底面和顶面都不水平）` +
        `${tilt && tilt !== 'none' ? `，坡度 ${tilt}` : ''} —— 必须写 ` +
        `"tilt": {"axis": "${tilt.startsWith('z') ? 'z' : 'x'}", "bottom": 格数, "top": 格数}` +
        `，只写 lean 是不够的：lean 只是墙斜，楼板还是平的`,
    )
  } else if (skind === 'soffit') {
    parts.push(
      `斜的形态：**斜底悬挑**（体块方正，只有底面被斜切）` +
        `${tilt && tilt !== 'none' ? `，坡度 ${tilt}` : ''} —— 写 ` +
        `"tilt": {"axis": "${tilt.startsWith('z') ? 'z' : 'x'}", "bottom": 格数}` +
        `（不要写 top，顶面要平）`,
    )
  }
  // 平面轮廓：这一条丢了，六边形楼层会被做成方盒子，两层看起来一样宽。
  const planShape = String(r.plan || '').toLowerCase()
  if (planShape && !['rect', '', 'unknown', 'none'].includes(planShape)) {
    const pname: Record<string, string> = {
      hex: '六边形（"shape": "hex" 或 "hex_front"）',
      oct: '八边形（"shape": "oct"）',
      round: '圆形/弧形（"shape": "round"）',
      wedge: '楔形（"shape": "wedge"）',
      l: 'L 形（"shape": "l"）',
    }
    parts.push(
      `一层平面：${pname[planShape] || planShape}` +
        `${r.plan_note ? ` —— ${r.plan_note}` : ''}（不要再用 w/d 拼方盒）`,
    )
  }
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
