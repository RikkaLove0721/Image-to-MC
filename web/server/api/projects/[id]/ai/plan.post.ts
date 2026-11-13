import fs from 'node:fs'
import path from 'node:path'
import { projectDir, readProject } from '../../../../utils/store'
import { runCore, runCoreJson } from '../../../../utils/core'
import { addUsage, chat, ensureVision, parseJsonLoose, type TokenUsage } from '../../../../utils/ai'
import { readPhoto, diffRender, readingNote } from '../../../../utils/vision'
import { enforceShape, enforceTilt, fixWallPalette } from '../../../../utils/geometry'

/**
 * spec 写法说明：这段是给模型的"格式契约"。
 * 只描述结构，不描述建筑 —— 建筑常识来自 mc12/knowledge.py 的 --rules。
 */
const SCHEMA = `你必须输出一个 JSON 对象（不要输出解释文字，不要用 markdown 代码块），结构如下：
{
  "name": "模型名",
  "subject": "building",
  "building": {
    "type": "三层住宅 / 联排 / 教学楼 / 图书馆 / 中式殿宇 / 法式宫殿 / 驱逐舰 ……",
    "meters": [14, 17, 11]
  },
  "palette": {
    "wall": "方块id", "trim": "方块id", "roof": "方块id",
    "glass": "方块id", "accent": "方块id", "floor": "方块id"
  },
  "volumes": [
    {"x":0,"y":0,"z":0,"w":20,"h":8,"d":12,"mat":"wall","floors":2},
    {"x":2,"y":8,"z":-2,"w":16,"h":9,"d":10,"mat":"wall","floors":2}
  ],
  "windows": [
    {"face":"south","volume":0,"u":2,"v":1,"w":3,"h":3,
     "cols":4,"rows":3,"gap_u":2,"gap_v":1,"mat":"glass","frame":1,"arch":false}
  ],
  "roof": {"type":"mansard","height":5,"mat":"roof"},
  "details": [
    {"type":"plinth","mat":"trim","h":1},
    {"type":"cornice","mat":"trim","out":1},
    {"type":"arcade","face":"south","volume":0,"u":2,"w":44,"h":4,"count":7},
    {"type":"portico","face":"south","volume":0,"u":18,"w":8,"h":7,"depth":2},
    {"type":"pediment","face":"south","volume":0,"u":18,"w":8,"h":3,"v":12},
    {"type":"door","face":"south","volume":0,"u":20,"w":3,"h":3,"steps":2},
    {"type":"dormer","face":"south","volume":0,"u":6,"w":2},
    {"type":"dormer","face":"south","volume":0,"u":14,"w":2},
    {"type":"balcony","face":"south","volume":0,"u":6,"v":8,"w":8,"depth":2},
    {"type":"curtain_wall","face":"south","volume":0,"u":2,"v":0,"w":12,"h":9,"mullion":3},
    {"type":"lancet","face":"south","volume":0,"u":2,"v":4,"w":2,"h":10,"cols":4,"gap_u":3},
    {"type":"rose","face":"south","volume":0,"u":14,"v":16,"d":7},
    {"type":"buttress","face":"south","volume":0,"u":0,"count":5,"spacing":8,"w":1,"h":16,"out":1},
    {"type":"pinnacle","face":"south","volume":0,"u":2,"count":5,"spacing":7,"h":3},
    {"type":"spire","volume":0,"h":18},
    {"type":"column","face":"south","volume":0,"u":3,"count":2,"spacing":10,"out":1},
    {"type":"canopy","volume":0,"u":8,"v":3,"w":5},
    {"type":"chimney","volume":0,"u":4,"w":2},
    {"type":"railing","volume":0,"mat":"iron_bars","face":"all"},
    {"type":"flagpole","volume":0,"u":6,"v":3,"h":10,"side":"east","flag":"wool:14"}
  ],
  "patches": [
    {"region":"hull_lower","mat":"-0.28","cover":0.9},
    {"region":"below_windows","mat":"-0.14","cover":0.35}
  ],
  "seed": 48213
}
约定：
- **shape / chamfer / poly / lean / skew / tilt 都是"按需"的形体字段**：
  只有照片里真的这么切了角、这么歪了才写。古典建筑（教堂、宫殿、城堡、民居）
  绝大多数是**正交体块 + 坡屋顶 + 塔楼**，照抄现代建筑那套会立刻走形。
- **subject 是你的主体判定**：building（建筑，默认）/ ship（舰船、军舰、货轮、客轮、航母）。
  舰船走另一套语法（长轴沿 Z、尖艏、甲板、上层建筑内倾），不要套建筑构件。
- **building.meters 是你对真实建筑/船的估算**：宽 / 高 / 深（船长写在第三位），单位米。
  看照片里门窗的层数、门的高度（约 2 米）来推。这一步决定模型比例，要认真估。
- 坐标 X 向东、Y 向上、Z 向南，单位是方块；volumes 的 x/y/z 是体块西北下角。
- mat 可以写角色名（wall/trim/roof/glass/accent/floor），也可以写具体方块 id 如 "concrete:0"。
- face 取 south/north/east/west；windows 的 u 是沿墙横向偏移，v 是竖直偏移。
- roof.type 取 flat / gable / hip / **mansard**（法式孟莎屋顶，宫殿府邸必用）。
- **taper 可选，用来做"一头尖"的形体**（舰艏、机鼻、锥形塔）：
  {"taper":{"axis":"z","end":"max","to":2,"len":26}} —— 沿 axis 收窄，end 是要收尖的那一端，
  to 是末端横截面尺寸（1~3 就是尖头），len 是收窄段长度。
- **shape 可选，用来做非矩形平面**（六边形楼层、八边形塔楼、楔形展厅、L 形转角楼）：
  "hex"（前后长边 + 左右收成 45° 尖）/ "hex_front"（前窄后宽，六边形楼层最常用）/
  "oct"（八边形）/ "wedge"（正面收成窄边）/ "round"（圆塔）/ "l"（L 形）/
  "poly" 自己给顶点（"poly":[[0,0],[20,0],[20,8],[14,16],[0,16]]，局部坐标 0..w）。
  轮廓会自动变成斜墙，窗、幕墙、檐口、女儿墙、勒脚、楼板全跟着走；
  只给 w/d 两个数，无论怎么调都还是一个长方形。
- **lean / skew 可选，用来做斜置体块**（上层与下层不平行）：
  "lean":{"x":5} 往右斜 5 格、"lean":{"z":4} 往前（朝街）斜 4 格、
  **"lean":{"x":5,"z":4} 两个方向一起斜** —— 照片里最常见的就是这种"斜着一个角挑出去"；
  "skew":{"z":6} 平面斜摆（俯视是平行四边形）。格数取体块高的 1/3 左右，上限 12。
  斜置体块的窗、幕墙、檐口、百叶会自动跟着斜，不用另写。
- **tilt 可选，用来做"斜着的楼板"**（这一层的底面/顶面本身不水平）：
  "tilt":{"axis":"x","bottom":8,"top":8} = 整层斜带，右端比左端高 8 格；
  只写 bottom = 斜底悬挑（盒子方正、底面被斜切）；只写 top = 斜屋面。
  简写 "tilt":{"x":7} 等于 bottom 和 top 同值。
  **lean 只是墙斜、楼板还是平的；照片里"二层像一条左低右高的带子"必须用 tilt**，
  否则这一层永远是水平的。斜带的窗、幕墙、百叶会按列自动跟着斜。
- details.type 共 20 种：
  plinth（勒脚，作用于所有体块，h 1~2）
  cornice（檐口，墙顶外挑 out 格，作用所有体块）
  door（入口门，u/w/h/steps，v 默认 0 即贴地）
  curtain_wall（玻璃幕墙，w/h 可以跨层，mullion 是竖梃间隔，transom 是横梁间隔）
  column（贴墙柱廊，count/spacing/out，古典立面必用）
  portico（门廊 = 柱子 + 顶板 + 台阶，w/h/depth）。**要气派的大门一律用它**：
    柱间是空的，人从柱子中间进去；door + canopy 叠出来的只是"墙上贴一块板"
  **bow_portico（弧形柱廊，白宫南门廊 / 圆厅别墅那类）：u 弧心、r 半径、**
    **count 柱数（5~8）、col_w 柱宽（大模型用 2）、spread 半张角（75~85）、h 柱高。**
    **柱子沿圆弧排开、檐部与顶部栏杆跟着弯。柱距 < 3 格会连成一排栅栏。**
  arcade（底层拱廊 = 柱 + 拱券，w/h/count，count 是开间数）
  pediment（三角山花，压在门廊顶上，w/h/v）
  dormer（老虎窗，坐在孟莎坡面上，u/w，v 可省）
  balcony / canopy / chimney
  railing（沿体块顶面**外缘**铺一圈栏杆，mat 建议 iron_bars，face 取 all/south/north/east/west）
  flagpole（细杆 + 旗：u/v 是杆在体块顶面上的位置，h 杆高，side 旗朝哪边，flag 旗的方块）
  **band（通长水平饰带，仅商业立面）：u/w/v/h，w 省略=整面宽，**
    **out 0（贴面换料）/ 1~3（外挑）。木纹招牌带、白檐口、檐下灯带都是它**
  **louver（竖向格栅/木百叶，仅商业立面）：u/w/v/h/step（每 step 格一根，默认 2）/out**
  **planter（花池，仅商业立面）：u/w/d（进深）/h，种 plant 指定的树叶，贴地一排**
  **signbox（招牌，仅商业立面）：u/v/w/h/out，mat 是底色，logo 取 m/arch/star/ring/bars/solid，**
    **logo_mat 是图案色，logo_v 是图案离地高度（不填居中）。w≥5 才画得下 logo**
  **↑ 这四类用于店面/餐厅/商铺，以及现代建筑（科研楼/办公/美术馆）的立面分格。**
  **住宅、学校、宫殿、教堂不要写 —— 它们靠勒脚 + 檐口 + 屋顶收头。**
  **lamp（灯具）：u/v/w/h/out（0=嵌进立面做灯带，1~2=外挂灯箱），**
    **mat 取 sea_lantern（海晶灯）/ glowstone（萤石）/ redstone_lamp（红石灯）。**
    **照片里成片的灯箱、招牌内透光、檐下灯带都用它，别拿白色混凝土画白板。**
  **grand_stairs（大台阶 / 爬坡道，山城、宫堡、高台基建筑必用）：**
    **u 起点、w 梯宽（垂直墙面 3~6）、y0 起步高度、y1 爬到的高度（相对该体块底，**
    **可以超过体块本身的高度）、turns 折返次数、run 每级踏步进深（默认 2）、**
    **rail 栏墙材质、rail_h 栏墙高出踏步的格数（0~1）。**
    **照片里"前方很多楼梯一圈圈往上绕"就是它：一条贴墙的折返坡道 + 白色栏墙，**
    **每级 1 格高。缺了它，爬在山上的宫殿会变成几摞摆在地上的白盒子。**
- windows 的 arch=true 会做成拱顶窗；h 可以大于 w（竖向长窗，最高 6）
- **patches 是材质替换**（只在 detail=4 观赏性细节时用）：把已经建好的方块换成
  同色系的另一种，用来做水线、墙脚返潮、窗下挂污、甲板分块。
  region 取 hull_lower / hull_side / deck / superstructure / base（墙脚）/
  roof / below_windows（窗下）/ walls / corners；
  mat 写 "-0.25"（暗一档）或 "+0.08"（亮一档），也可以写具体方块 id；
  cover 是覆盖率 0.02~1，≥0.9 表示整片连续（水线用这个，别打成斑点迷彩）。
- 小件（设备箱、天线、外机、水箱）不需要坐标，构建端按主体类型自动摆。
- 方块 id 必须是 1.12.2 存在的（例如 "concrete:0"、"stained_hardened_clay:0"、
  "planks:5"、"stonebrick"、"stained_glass:9"、"hardened_clay"）。
- **玻璃默认是玻璃板**：窗、幕墙、拱廊的玻璃会自动换成染色玻璃板（102），
  不用你写板名。只有玻璃天花板、采光顶、玻璃盒子幕墙、厚重防护玻璃
  才在对应那条上加 "thick": true（用整块玻璃 95/20）。
- 绿化带：花池顶层铺 grass（草方块），上面点 tallgrass（草丛），
  零星插 yellow_flower（蒲公英）/ red_flower（虞美人）。
- 石英有三种变种：quartz_pillar（竖纹，做柱子）、
  chiseled_quartz_block（錾制）、smooth_quartz（平滑，做收边）。`

/** 从模型回复里抠出蓝图；带 ```json 包裹、尾逗号、前后解释都能救回来。
 *
 * 这里直接用 ai.ts 的 parseJsonLoose，不再本地写一份 JSON.parse ——
 * 本地那份只会去尾逗号，**不会补被截断的括号**。蓝图字段一多（details 十几个、
 * volumes 三四个、现在还有 shape/poly/lean），被 max_tokens 砍在最后一个
 * 大括号前的概率明显变高，用户看到的就是"AI 返回的蓝图出错"。
 */
function parseSpec(raw: string): any | null {
  const spec = parseJsonLoose(raw)
  return spec && typeof spec === 'object' ? spec : null
}

export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id') || ''
  const project = readProject(id)
  const body = (await readBody(event)) || {}
  const front = project.images.front
  if (!front) throw createError({ statusCode: 400, statusMessage: '先上传正视图' })

  const photo = path.join(projectDir(id), 'images', front.file)
  // 用户显式指定的"模型总宽（格）"。0 / 未传 = 自动（按 AI 估的米数 × 比例尺）。
  // 这一项存在的原因是：AI 估米数经常只有实际的一半，巴黎圣母院西立面 41 米被估成
  // 18 米，乘 2 格/米就是 36 格 —— 36 格宽的哥特教堂连玫瑰窗都挖不出来，
  // 出来是个灰盒子。给用户一个直接拧的旋钮，比继续猜米数靠谱。
  const forcedW = Math.round(Number(body.targetWidth) || 0)
  const width = String(Math.min(132, Math.max(16, forcedW || Number(body.width) || 40)))
  // 游戏建筑技巧性：0 低（尽量还原照片）/ 1 中（默认）/ 2 高（优先社区法则）
  const craft = Math.max(0, Math.min(2, Math.round(Number(body.craft ?? 1) || 0)))
  const brief = runCoreJson<any>(['--brief', '-i', photo, '--width', width])

  // ---- 先测一下这个模型到底能不能看图 ----
  // 能力按模型分、还会随服务端升级悄悄变：同一个端点下 deepseek-flash 能读图，
  // deepseek-v4-pro 却把图变成 [Unsupported Image]。所以不预设、每次都问一句
  // （结果缓存 7 天，换模型或点"重新检测"才重测）。
  const vprobe = await ensureVision()
  // 本地分析只能量像素：宽高比多少、有几段体块、主色是什么。它答不出
  // "这是法式宫殿还是驱逐舰"、"屋面占立面的三成"、"左边有一排拱廊"。
  // 所以先让视觉模型把照片读成一份结构化判读，再交给主模型写蓝图。
  // 视觉通道吃原图会被接口拒：5MB 的 PNG base64 之后 7MB，返回 413，
  // readPhoto 静默吞掉 → 界面上看着像"没开视觉"。先缩一张 200KB 的小图。
  // 缩图失败就退回原图：宁可赌一次，也不要直接丢掉"看图"这条路。
  const visionPhoto = path.join(projectDir(id), '_photo_vision.jpg')
  let photoForVision = photo
  if (vprobe?.ok) {
    try {
      runCore(['--thumb', photo, visionPhoto])
      if (fs.existsSync(visionPhoto)) photoForVision = visionPhoto
    } catch (e: any) {
      console.error('[plan] 缩图失败：', String(e?.message || e).slice(0, 200))
    }
  }
  const reading = vprobe?.ok ? await readPhoto(photoForVision) : null
  // ---- 主体类型决定"用哪套语法" ----
  // 建筑和舰船是两套完全不同的建模语法：建筑讲开间、层高、勒脚檐口；
  // 舰船讲长宽比、尖艏、甲板、上层建筑内倾、栏杆旗杆。以前全家共用
  // 一份建筑规则，AI 拿到军舰照片也只能写出一栋长条形写字楼。
  const subjRaw = String((reading as any)?.subject || 'building').toLowerCase()
  const isShip =
    ['ship', 'vessel', 'warship', 'boat'].includes(subjRaw) || subjRaw.includes('ship')
  const cm = (reading as any)?.commercial
  // ---- 第三种语汇：现代建筑（错位堆叠 + 悬挑 + 幕墙 + 竖百叶）----
  // 缺这一支的时候，实验室那张照片被做成了"孟莎屋顶 + 老虎窗 + 门廊 + 山花"的府邸：
  // 通用规则只教了住宅和古典，AI 又只拿到"白墙 + 宽高比 1.8"，只能往府邸上靠。
  // 判定按**类型词**来；没有视觉判读时退一步用照片特征兜底（近白墙 + 量不到窗阵），
  // 这两条正好是"整片玻璃幕墙"的样子，而玻璃幕墙的照片本来就数不出窗格。
  const typeText = String((reading as any)?.type || '')
  const MODERN_RE =
    /现代|当代|实验|科研|办公|写字楼|总部|美术馆|展览|展馆|展示中心|科技|综合体|艺术中心|体育馆/
  const bareHint =
    !reading &&
    (brief as any)?.windows?.detected === false &&
    // 只认**真白**的墙。早先把 concrete:8（浅灰混凝土）也算进来了，
    // 结果哥特教堂那种深灰石墙命中 bareHint，被判成"现代玻璃楼"，
    // 走现代语汇 → 平顶 + 竖百叶 + 幕墙，一栋大教堂变成灰盒子。
    /quartz|concrete:0|white_wool|snow/.test(String((brief as any)?.palette?.wall || '')) &&
    (Number((brief as any)?.building?.aspect) || 0) >= 1.35 &&
    // 高宽比过大的多半是塔楼/教堂，竖着长，不是"横向铺开的玻璃盒"
    (Number((brief as any)?.building?.aspect) || 0) <= 2.4
  const isModern = !isShip && (MODERN_RE.test(typeText) || bareHint) && !cm?.is_commercial
  const isShop =
    !isShip &&
    !isModern &&
    (!!cm?.is_commercial ||
      ['shop', 'store', 'storefront', 'retail', 'commercial', 'restaurant', 'office'].includes(
        subjRaw,
      ) ||
      /店|商铺|餐厅|商业|门面|快餐|超市|银行|营业厅|展厅/.test(
        `${(reading as any)?.type || ''}${(reading as any)?.subject || ''}`,
      ))
  const rulesArgs = [
    '--rules',
    ...(isShip ? ['ship'] : isShop ? ['shop'] : isModern ? ['modern'] : []),
    '--craft',
    String(craft),
  ]
  // 判读结果用一段紧凑的文字并进提示词（见 readingNote），不再塞回 brief 的 JSON：
  // 同一份信息出现两遍要多花两三百 token，而模型并不会因此更准。
  const visionFloors = Number(reading?.floors) || 0
  const visionBays = Number(reading?.bays) || 0

  // 种子：这一次生成的身份。同一个种子 + 同一份蓝图 = 同一个模型，
  // 用户回填种子就能复刻，也方便把"哪次生成的、得分多少"记进经验库。
  const seed = Math.max(1, Math.min(999999, Math.floor(Number(body.seed) || 0)) ||
    (10000 + Math.floor(Math.random() * 89999)))

  const extra = String(body.hint || '').trim()
  const aspect = Number((brief as any)?.building?.aspect) || 0
  const aspectOk = (brief as any)?.building?.aspect_confident !== false
  const winDetected = (brief as any)?.windows?.detected !== false
  const aspectNote =
    aspect > 0
      ? `\n- 照片里建筑的高宽比约 ${aspect}（宽 ÷ 高，${aspectOk ? '可信' : '仅供参考'}）。` +
        `你估的 meters 换算出来应该接近这个比值${craft >= 2 ? '（风格档为高，可放宽 ±20%）' : ''}，差太多说明米数估错了，回去再估一遍。` +
        `注意这是**斜俯视**照片量出来的，真实高度只会更高、比值只会更小。` +
        `\n- 换成格数要能对得上：**蓝图总高（最高体块的 y+h）≈ 总宽 ÷ ${aspect}**。` +
        `比如宽 48 格时总高就该是 ${Math.round(48 / aspect)} 格；只写 18 格，` +
        `做出来就是一条扁长盒子，照片里的竖向体量全没了。` +
        `三层玻璃楼的高度一般这样分：底层通高 8~10、中段 4~6、上层 8~10。`
      : ''
  const wideNote = winDetected
    ? ''
    : `\n- 照片的窗阵没有可靠检测出来：不要照抄 windows.cols/rows，按层数和墙宽自己排（每 4~6 格一扇）；`

  // ---- 游戏建筑技巧性：这是"还原照片"和"好玩"之间唯一的一根滑条 ----
  // 低：照片说一不二，宁可难看也要像。高：按社区那套形式法则来，
  // 允许改配色、改开间节奏、加照片里没有但会更好看的构件。
  const craftNote =
    craft >= 2
      ? `\n\n【风格档：高 · 优先 MC 社区建筑法则】` +
        `\n- 这一版**不是逐像素复刻照片**，而是"以照片为题的 MC 建筑"。` +
        `照片的配色、开间数、构件位置都只作参考：如果社区法则（三色原则、开口包框、` +
        `腰线分段、收分、凹进）跟照片有冲突，**按社区法则来**。` +
        `\n- 允许为了好看改动：把碎窗合成整排、把杂色统一成三色、给开口加框、` +
        `在顶部加挑檐、把入口做深一进（凹进 1 格）。` +
        `\n- 但**体量关系不能改**：层数、宽高比、大体块的主次和退台要照着照片。` +
        `\n- 宽高比可以放宽到照片的 ±20%（为了让步给好看的开间节奏）。`
      : craft === 0
        ? `\n\n【风格档：低 · 以还原照片为准】` +
          `\n- 优先照抄照片：开间数量、窗的位置、配色、构件分布都尽量贴着照片，` +
          `不要为了"好看"改动结构。社区那套形式法则只在照片看不出来的地方兜底。`
        : `\n\n【风格档：中 · 还原为主、法则兜底】` +
          `\n- 以照片为准，在此基础上套用社区通用做法（三色原则、开口包框、` +
          `腰线分段、落地收勒脚），不改动体量与开间数量。`
  // ---- 补全：照片被裁掉的一侧 ----
  // 这是"自动补全图片建筑"的入口。照片只拍到半栋楼时，模型照抄可见部分，
  // 产出的永远是半栋；所以要把"整栋有多宽、要补几个开间"直接写进要求。
  const comp = (brief as any)?.completion || {}
  // 两路裁切判断：视觉模型看得懂"转角收没收头"，像素检测看得懂"掩码碰没碰边"。
  // 视觉给了就用视觉的（它更懂建筑），没给才退回像素 —— 背景里的另一栋楼
  // 也会碰到画面边，纯像素判断会把"没被裁"误判成"被裁"。
  const vCut: string[] = []
  if (cm?.cut?.left) vCut.push('left')
  if (cm?.cut?.right) vCut.push('right')
  const cutSides: string[] = vCut.length
    ? vCut
    : Array.isArray(comp.cut)
      ? comp.cut
      : []
  const compRatio = Number(comp.ratio) || 1
  const compBays = Number(comp.bays_full) || 0
  const compVisible = Number(comp.bays_visible) || 0
  const completeNote = cutSides.length
    ? `\n- **照片有裁切，必须补全**：${cutSides.map((c) => (c === 'left' ? '左' : '右')).join('、')}边缘的建筑被画框切断了，` +
      `照片里只是整栋的一部分。` +
      (compBays > 0
        ? `完整立面约 ${compBays} 个开间（照片里只看得见 ${compVisible} 个），`
        : '') +
      `总宽至少是可见宽度的 ${compRatio} 倍。` +
      `补全时**只复制结构**（玻璃幕墙、水平饰带、格栅、花池、开间节奏），` +
      `**不要复制招牌** —— 招牌只在照片里出现的那一处，复制两个就成了广告牌。` +
      `加宽的办法是**把同一个体块加长**，不是加两侧的翼楼。` +
      `入口保持在整栋的中轴或黄金分割点。`
    : ''
  const detail = Math.min(4, Math.max(1, Number(body.detail) || 2))
  // 观赏性细节（等级 4）不是"再多几条线脚"，是另一层：小件堆叠 + 材质色差。
  // 它要求最高精度打底，所以等级同时抬到 3 以上的那套要求。
  const showpiece = detail >= 4
  // 规则文本要等 detail 定下来才知道要不要附带「观赏性细节」那一节
  const rules = runCore(showpiece ? [...rulesArgs, '--ornament'] : rulesArgs).stdout.trim()
  const floorsWanted = Number(body.floors) || 0
  const floorsHint = floorsWanted || Number((brief as any)?.windows?.floors_hint) || 3
  const floorsN = floorsWanted || visionFloors || floorsHint
  // 尺寸 = 比例尺（1 米 = 多少格），由用户滑条给，只是偏好
  const scale = Math.min(8, Math.max(0.5, Number(body.scale) || 2))
  // 结构数量 1~5：建筑由几个部分组成（角塔/侧翼/门厅/凸窗/屋面设备）。
  // 这一档**不交给 AI** 写：它一次只能写得动三五个体块，写三十个坐标必歪。
  // 计划端只负责把等级带进蓝图，真正的体量由构建端长（mc12/spec.py）。
  const complexity = Math.max(1, Math.min(5, Math.round(Number(body.complexity) || 2)))
  const sizeNote =
    `\n- **先估真实尺寸，再乘比例尺**。估尺寸的步骤（不要跳步）：` +
    `① 数立面有几排窗带 = 层数；② 层高按用途取 3.2 米（住宅/办公）/ 3.8 米（教学/图书馆），` +
    `宫殿/府邸这类大层高的取 4.5~5.5 米；` +
    `③ 总高 = 层数 × 层高 + 屋顶；④ 宽度用"门约 1 米宽、窗约 1.5 米宽"反推，` +
    `数一下立面有几扇窗；⑤ 自查：三层住宅应在 9~12 米高，教学楼 12~15 米，` +
    `算出 7 米高或 30 米高的都说明估错了。` +
    `\n- **尺寸由比例尺决定**：用户选的是 **${scale} 格 / 米**。` +
    `把估出的宽/高/深（米）乘这个数得到格数：` +
    `模型总宽 = 宽(米) × ${scale}，总高 = 高(米) × ${scale}，总深 = 深(米) × ${scale}。` +
    `这是**偏好而非硬指标**：如果按它算出来门窗放不下（层高 < 3 格）或过于空旷（层高 > 8 格），` +
    `可以在 ±25% 内浮动，但不要差一倍。` +
    `\n- 层数约 ${floorsN} 层（用户填写优先，否则按照片估），` +
    `层高 = 总高 ÷ ${floorsN}，正常落在 3~6 格；细节等级 ${detail} 时建议 ` +
    `${detail >= 3 ? 5 : detail === 2 ? 4 : 3} 格。`
  // 尺寸越大越还原：把预设宽度换算成细节等级，写进提示词也写进蓝图
  const BY_LEVEL: Record<number, string> = {
    1: `\n- 这是一版小尺寸：2 个体块、窗 2~4 列、细节 4 处以上即可，别把模型塞满；`,
    2: `\n- 这是一版中尺寸：2~3 个体块，窗列按墙宽铺满（4~6 列），` +
       `细节 6 处以上：门 + 台阶、勒脚、檐口、至少一处阳台或幕墙；`,
    3: `\n- 这是一版大尺寸：**要做细**。3 个以上体块（主体 + 两侧翼或退台），` +
       `窗列 5~8 列、每层都要有窗，细节 8 处以上：入口门廊 + 台阶 + 雨棚、勒脚、檐口、` +
       `多层阳台或整片幕墙、屋顶设备（烟囱/水箱/楼梯间）至少一处、侧立面与正面对齐；`,
    4: `\n- 这一版是**观赏性细节**：先把上面"精细"那一档**做满**（体块拆开、每层有窗、` +
       `细节 8 处以上），再补一层"人味"。多出来的这层不是靠加构件数量，而是：` +
       `\n  · patches 写 2~4 条材质替换 —— 照照片里本来就脏、就换过料的地方，不要凭空均匀做旧；` +
       `\n  · 小件（设备箱、天线、外机、水箱）由构建端自动摆放，它们**成组、不等距、只在一侧**；` +
       `\n  · 你要留出"能放东西的地方"：屋面至少一段是平的（别整栋坡顶），` +
       `甲板/平台不要被小体块占满；` +
       `\n  · 对称摆件是这一档最容易犯的错 —— 左右镜像的小件一眼就是玩具。`,
  }
  // 舰船和建筑是两套提问方式：给船的提示词里不能出现"楼/层高/开间/屋顶"，
  // 否则模型会顺着这些词把船写成一栋长条写字楼（实测过）。
  const head =
    `这是一张照片的自动分析结果（单位：方块格）：\n` +
    JSON.stringify(brief) +
    readingNote(reading) +
    (extra ? `\n\n额外要求：${extra}` : '')

  const bowDir: Record<string, string> = {
    left: '画面左侧',
    right: '画面右侧',
    toward: '正对镜头',
    away: '背对镜头',
  }
  const bowRaw = String((reading as any)?.ship?.bow || '')
  const bowNote =
    bowRaw && bowRaw !== 'none'
      ? `\n- 视觉判读：照片里船头朝${bowDir[bowRaw] || bowRaw}。` +
        `照片角度不影响产出，但你要把它转成"模型里船头朝南（+Z）"。`
      : ''
  const railNote = (reading as any)?.railing?.has
    ? `\n- 视觉判读：照片里**有栏杆**（${(reading as any)?.railing?.where || '甲板/平台边缘'}），` +
      `在对应位置铺 railing，不要漏。`
    : ''
  const flagNote = (reading as any)?.flagpole?.has
    ? `\n- 视觉判读：照片里**有旗杆**（${(reading as any)?.flagpole?.where || '甲板/桅顶'}），` +
      `用 flagpole 放在同样的位置。`
    : ''

  const shipAsk =
    `\n\n请据此设计一个 Minecraft 1.12.2 的**舰船**模型。要求：` +
    `\n- 蓝图第一层写 "subject": "ship"；` +
    `\n- **朝向**：船长轴沿 Z 轴（南北），**舰艏在 z 最大端**（南）。` +
    `照片是什么角度都无所谓，模型里一律"船头朝南"，这样正面视图看到的就是迎面驶来的船头，` +
    `而不是一堵船舷。船头朝哪边先看照片判断，但最终都要转成这个朝向；` +
    `\n- **甲板上的东西全部朝向舰艏**：主炮、垂直发射装置、舰桥、桅杆、烟囱、直升机甲板` +
    `沿船长从船头到船尾**依次排布**，舰桥的正立面朝 +Z；` +
    `\n- **舰艏必须收尖**：主船体加 "taper":{"axis":"z","end":"max","to":2,"len":船长×0.3}，` +
    `to 取 1~3。平头的模型一眼就不是船，这是这张图的第一识别特征；` +
    `\n- **长宽比 6~10**：船长是船宽的 6~10 倍（80 格长的船就写 10~13 格宽），` +
    `这是"像不像船"的第一道门槛；` +
    `\n- 船体分两层：主船体 h 4~6 + 甲板 h 1（mat 用 trim，同样带 taper，` +
    `否则船头会露出一截没有甲板的船体）；` +
    `\n- 上层建筑在船中偏后，3~4 段逐层内收（每段宽/深各减 1~2 格），` +
    `舰桥正面用一条横向玻璃带（w 大 h 2），不要排一排小方窗；` +
    `\n- 桅杆用 w/d 1~2、h 6~10 的细体块压在上层建筑顶部；` +
    `\n- **舰艏甲板不要空着**：主炮用 3~4 格的小体块压在艏部甲板上，` +
    `舰尾留直升机甲板（一块平板），这是照片里最容易被看出来的两处；` +
    `\n- **details 必须包含**：railing（甲板边缘一圈 + 上层建筑平台，mat 写 iron_bars）、` +
    `flagpole（舰尾或桅顶，h 8~12，flag 写 wool:14）；` +
    `\n- **不要写** plinth / cornice / door / balcony / arcade / portico / pediment / dormer —— ` +
    `这些是建筑构件，装在船上整艘船立刻变回写字楼；` +
    `\n- roof 只能写 {"type":"flat","parapet":0}；` +
    `\n- 窗只开在舰桥和上层建筑：1~2 条窗带；**船体（最下面那层大块）侧面一格窗都不要开**，` +
    `照片里那些亮点是设备反光，不是舷窗。` +
    (showpiece
      ? `\n- **观赏性细节**：甲板上的设备箱、天线、救生筒、甲板灯会由构建端成组摆放，` +
        `你不用写它们的坐标；你只负责材质色差 —— patches 写：` +
        `hull_lower（水线，mat "-0.28"、cover 0.9）、deck（甲板分块，"-0.08"、0.14）、` +
        `superstructure（"+0.06"、0.1）。照片里船体如果上下明显两种灰，再加一条 hull_side` +
        `（"-0.10"、cover 0.15~0.25，别超过 0.3 —— 覆盖率高就不是"钢板色差"而是迷彩了）。` +
        `甲板上的大件（主炮、垂发、机库门、舰岛）仍然要用体块写，小件补不了它们。`
      : '') +
    bowNote + railNote + flagNote

  // 商业立面和住宅是两套语汇。给店面的提示词里出现"坡屋顶/阳台/层高"，
  // 模型就会把它盖成一栋带门脸的小楼 —— 这正是"店面做成住宅"的成因。
  const shopAsk =
    `\n\n这是**商业立面**（店面 / 餐厅 / 商铺），不是住宅。请按商业语汇设计：` +
    `\n- building.type 写"商业店面 / 快餐店"这类；roof 必须是 {"type":"flat","parapet":1,` +
    `"mat":"concrete:8"}（浅灰，深色屋面在等轴测里像一块黑板）；` +
    `\n- **只写 1 个体块**：店面是一栋连续的薄片建筑，把补全后的总宽直接写进 w，` +
    `**不要拆成"主体 + 两翼"** —— 那样细节只会落在中间那个体块上，` +
    `两侧变成两片空白墙，模型立刻散成三栋；` +
    `\n- **不要写 windows**：店面没有"一扇扇窗"，玻璃全部交给 curtain_wall；` +
    `\n- **palette.glass 用深色玻璃**：stained_glass:15（黑）或 :7（灰）。` +
    `商业店面的玻璃是镀膜反射的深灰，不是蓝色鱼缸；` +
    `\n- **竖向序列一段都不能少**（从下往上，店面就看这条线）：` +
    `深色勒脚（plinth h 1~2）→ 一楼通高玻璃（curtain_wall h 5~7，` +
    `mullion 4、transom 3~4、**frame_mat 必须写深色 concrete:7**）` +
    `→ **楼板线/雨棚（band h 1、out 1、mat quartz_block:0）**` +
    `→ 二楼玻璃（**再写一条** curtain_wall，h 3~6，` +
    `**u 与 mullion 必须和一楼完全一致**，竖梃才上下对齐）` +
    `→ 木纹招牌带（band h 2、out 1、mat planks:1）→ 女儿墙；` +
    `\n- **照片里二楼有玻璃/有窗带，就必须做两层**：两层店面写 **两条 curtain_wall**，` +
    `中间夹那条白色 band 就是二楼楼板；只写一条横跨两层高，` +
    `远看就是"一层挑高大堂"，不是两层店。floors 要跟着写对（两层写 2），` +
    `体块 h = 两层之和（店面首层通高，7~9 格一层是正常的，不是错误）；` +
    `\n- 玻璃覆盖立面 50%~75%：两条 curtain_wall 都铺满整面（w = 体块宽 − 2），别留大片实墙；` +
    `\n- **最上面还有一层、而且更小/偏一侧时，写第二个体块**（不要挤在同一个体块里）：` +
    `y 接在基座顶面、x 贴到要偏的那一侧、z 内缩 1~2 格、w/d 收窄到基座的 55%~70%，` +
    `floors 1；它的玻璃另写一条 curtain_wall 并标 **"volume": 1**，` +
    `frame_mat 用浅色 quartz_block:0（深色塔身配白竖梃）、mullion 5~6` +
    `（太密会读成"一排小窗"）。**这个体块不要写 plinth** —— 它坐在屋顶上；` +
    `\n- **竖向招牌柱 signbox 必须有，它是这张图的第一识别特征**：` +
    `u ≈ 宽的 15%~25%、**v=0、h = 体块总高（从地面一直通到顶，不要停在招牌带下面）**、` +
    `w 5~6、out 1、mat 用照片里的品牌色（红色写 concrete:14）、logo "m"、` +
    `logo_mat "concrete:4"（黄）、**logo_v 落在二楼那一层**（图案在二楼玻璃的高度，` +
    `不是贴在顶上）。**h 是竖直高度，h=2 是一条横招牌，不是柱子** —— ` +
    `这是最容易写错的一个字段；` +
    `\n- 入口是**双开玻璃门**：door w 4~6、h 4~5、` +
    `**mat 写 "stained_glass:15"（玻璃门，不是木板门）**、frame_mat "concrete:7"、steps 1；` +
    `\n- 转角与设备遮挡用 louver（step 2、out 1）；贴地一排 planter（d 2、plant 树叶），只在一侧；` +
    `\n- **进深不要太大**：店面是"薄片"建筑，d 取宽的 25%~40%，` +
    `再大就成了一块厚砖头，正面反而看不清；` +
    `\n- **不要**写 balcony / dormer / arcade / column / portico / pediment / chimney —— ` +
    `给店面加坡屋顶和老虎窗，一眼就是"住宅冒充商铺"。`

  // 现代建筑（科研楼 / 办公 / 美术馆 / 综合体）是**第三套语汇**。
  // 它跟住宅的差别不是"细节多少"，而是形体来源：住宅靠主楼 + 两翼 + 屋顶，
  // 现代建筑靠**上下错位的盒子 + 悬挑 + 整片玻璃 + 竖百叶**。
  // 拿住宅那套问它，得到的是"一座孟莎屋顶的白石府邸"（实验室那张就是这么废掉的）。
  const modernAsk =
    `\n\n这是**现代建筑**（科研楼 / 办公 / 美术馆 / 综合体），请按现代语汇设计：` +
    `\n- building.type 写"现代实验室 / 现代办公楼 / 美术馆"这类；` +
    `roof 必须是 {"type":"flat","parapet":1}（平顶 + 女儿墙），` +
    `**不要** mansard / gable / hip / dormer / chimney / arcade / portico / pediment / column；` +
    `\n- **体量是错位堆叠的盒子**，不是主楼 + 两翼：写 2~4 个体块，` +
    `下层顶面 = 上层的 y（例如下层 h 8 → 上层 y 8）；` +
    `平面上每个盒子各偏 2~6 格（x 或 z 错开），**别在 x 上首尾对齐、也别一样高**；` +
    `**上层不要层层缩小**：相邻两层的 w/d 差控制在 4 格以内，靠错位错开；` +
    `每层都比下层小一圈，出来的是梯田，下半层的屋面会露一大片；` +
    `\n- **看照片里上层是不是斜的**：如果上层盒子与下层不平行（斜着挑出去、` +
    `立面是个平行四边形），就必须给它加斜置参数 —— 光靠 x/y/z 平移，` +
    `做出来永远是一摞正交方块：` +
    `\n  · "lean": {"x": 5} = 整个体块往上、往右斜 5 格（剖面斜，正面看得见斜边）；` +
    `\n  · "lean": {"z": 4} = 整个体块往前（朝街）斜 4 格；` +
    `\n  · **"lean": {"x": 5, "z": 4} = 既朝外又朝前，两个方向一起斜** ——` +
    `照片里那种"斜着一个角挑出去"的盒子就是这种，只写一个方向，做出来只是"半个斜"，` +
    `正面看还是方块；判断方法：边线是斜的（x）+ 看得见盒子的底面/下檐（z）；` +
    `\n  · "skew": {"z": 6} = 沿进深方向横移 6 格（平面斜，俯视是平行四边形）；` +
    `\n  lean 的格数取体块高的 1/3 左右（h 12 → 4~6 格），单个方向不超过 8；` +
    `**只给上层的一到两个盒子**，底层保持水平；skew 取 4~10；` +
    `斜置体块的女儿墙、檐口、百叶会自动跟着斜，不用另外写；` +
    `\n- **但更常见的是"整层斜带"**：照片里中间那一层的**楼板本身是斜的**` +
    `（左低右高、或近高远低），像一条斜着搭上去的带子。这种 lean 做不出来 ——` +
    `lean 只让墙斜，每层楼板仍然是平的，远看还是水平的三明治。必须用 tilt：` +
    `\n  · "tilt": {"axis": "x", "bottom": 8, "top": 8} = 整层斜带，右端比左端高 8 格；` +
    `\n  · "tilt": {"axis": "x", "bottom": 6} = 只有底面斜（悬挑盒的斜底，顶面还是平的）；` +
    `\n  · 坡度取体块长度的 1/5~1/4（40 格宽 → 8~10 格），小于 4 格等于没斜；` +
    `\n  · tilt 的楼板、女儿墙、百叶会**按列**自动跟着斜，幕墙照常写 w/h，不用自己算斜边；` +
    `\n  · **相邻两个盒子的斜向经常相反**：一个"左低右高"、另一个"左高右低"（向中间收，` +
    `或向两边张开）。逐个盒子看它自己的底边，别给所有体块写同一个符号 ——` +
    `统一一个方向做出来是"整栋歪了"，不如照片里那种左右错动；` +
    `\n- **平面轮廓不一定是方的**：照片里转角被切掉一块、整层看着像六边形，` +
    `就必须给**那一层**加轮廓参数 —— 用 w/d 硬凑永远是长方形：` +
    `\n  · **优先 "chamfer": 5~8**（矩形四角斜切）—— 建筑里的"六边形楼层"` +
    `九成是这种：前后长边完整，只有转角被斜切，正面的玻璃墙照样是一整条；` +
    `\n  · "shape": "hex" = 真正的六角形（左右两端收成尖），只给六角展厅/塔楼用；` +
    `\n  · "shape": "hex_front" = 前窄后宽（正面变成两条斜边），会把正面玻璃拆成两段；` +
    `\n  · "oct" 八边形 · "wedge" 正面收成窄边 · "round" 圆塔 · "l" L 形；` +
    `\n  · 顶点特殊时用 "poly": [[0,0],[24,0],[24,9],[16,18],[0,18]]（局部坐标 0..w）。` +
    `\n  轮廓会自动变成斜墙，幕墙/窗/檐口/女儿墙/勒脚全都跟着走；` +
    `**六边形的楼层，正面比 w 窄得多**，幕墙的 w 取 (w − 2 × 切角) 而不是 w − 2，` +
    `否则玻璃会跑到切角上去；` +
    `\n- **"玻璃基座 + 斜带 + 双盒"的组合**（现代实验楼/美术馆最典型，照这个数写）：` +
    `\n  · 底层："chamfer": 6（四角斜切，前后长边完整）、w 取总宽、d 取 w×0.55、` +
    `h 8~10、floors 2~3；正面一条通高幕墙（w 取 w−2×切角、thick:true），两侧留白墙；` +
    `\n  · 中层（关键的一层）：y = 底层顶面、w 比底层**宽 6~10 格**（左右都探出去）、` +
    `h 4~6，正面整条幕墙 + 通长 louver，再加 **"tilt": {"axis":"x","bottom":8,"top":8}**；` +
    `\n  · 上层：两个盒子坐在斜带上，**各自 "tilt": {"axis":"x","bottom":8}**` +
    `（坡度与斜带一致，盒子底才贴得住带子）、y 取斜带**低端**的高度，` +
    `中间留 3~5 格玻璃缝，两个盒子前后错 2~4 格、高度差 1~3 格；` +
    `\n  · 斜的那一层要留出净立面（幕墙或百叶），别用细节糊满，斜面读不出来等于没做；` +
    `\n- **至少一处悬挑**：上层体块的 w/d 比下层**大 2~4 格**，或者 z 比下层**小 2~4 格**` +
    `（向南探出）。探出去那几格的底面就是雨棚 —— 所以**悬挑下方必须空着**，` +
    `不要为了"支撑"在下面补一个撑到地面的体块，那样悬挑会读成"墙厚了一截"；` +
    `\n- **底层内收**：首层体块比上层窄 2~4 格、z 退进 2~3 格，做成通高玻璃（h 6~9），` +
    `上层压上去，整栋才有"浮起来"的感觉；` +
    `\n- **正面主体是幕墙，不是一扇扇窗**：每个朝南的体块写一条 curtain_wall，` +
    `w = 体块宽 − 2（铺满整面）、h 取「体块高 − 1」（顶上那一行留给檐口/女儿墙）、` +
    `mullion 5~7、transom 4~6、**"thick": true**（整块玻璃 —— 玻璃板在 1 格尺度上` +
    `读成一格格小窗，幕墙必须是整块才连续）、frame_mat 写深色 concrete:7 或白 quartz:0。` +
    `windows 最多留 1 条给侧立面；` +
    `\n- **竖向百叶 louver 必须有，至少一段**（这是这类立面最强的识别特征）：` +
    `同一条 u 上先写 curtain_wall 再写 louver，louver 的 step 2、out 1、` +
    `h 覆盖整层、mat 用白色 concrete:0 —— 百叶后面仍然是玻璃，不是墙；` +
    `\n- 屋面材料取墙色系（白或浅灰），**不要深灰**：一大片深灰屋面会把整栋白楼压成工业厂房；` +
    `\n- 屋顶再压一个 1~2 格高的小体块当设备间（别铺满屋面）；` +
    `\n- 配色：白 / 浅灰墙 + 冷色玻璃（stained_glass:9 或 :11）+ 一处点缀` +
    `（木色 planks:1 或橙 concrete:1），深色只用在框、勒脚、屋面设备上；` +
    `室内灯带用 sea_lantern（冷白），**不要 glowstone**（暖黄，在白立面上像一条黄胶带）；` +
    `\n- 层高 4~5 格，首层 6~9 格；**floors 按立面数，别拿 floors 去凑高度**。` +
    `\n- 三段式玻璃楼的**标准配方**（按比例套数，别自己发明一套，实测这样最像）：` +
    `\n  · 底层：x=4, z=6, w=总宽−8, h=8 → 退进的通高玻璃门厅；` +
    `\n  · 中段：x=0, z=3, w=总宽, h=4 → 一条整片白带 + 竖百叶，前后各探出 2 格；` +
    `\n  · 上层左盒：x=0, y=中段顶面, z=0, w=总宽×0.45, h=9；` +
    `\n  · 上层右盒：x=总宽×0.52, y=中段顶面, z=5, w=总宽×0.45, h=10；` +
    `\n  两个上层盒子**进深差 4~6 格、高度差 2~4 格**，其中一个加 lean 或 skew 就是斜置；` +
    `\n  这套数字不是死规定，但**尺寸关系要守住**：上层两个盒子加起来盖住总宽的 90%~100%，` +
    `层间 y 严丝合缝，进深差 ≥4 格。`

  // 现代建筑的正面是整片玻璃：按"窗列铺满"去要求它，等于把幕墙切成一排小窗。
  const windowNote = isModern
    ? `\n- **正面不要用 windows 排小窗**：改用 curtain_wall 铺满（w = 体块宽 − 2、` +
      `h 取 4~9），windows 只留给侧立面（1 条、cols 2~3）；`
    : `\n- 窗阵按 windows 里的节奏排，但**列数要重新按墙面宽度铺满**：` +
      `cols ≈ (墙面宽 − 2×边距 + 间距) ÷ (窗宽 + 间距)，覆盖立面 55%~90%，` +
      `别把窗挤在一头、另一边留一大片实墙；` +
      `\n- gap_v 取 2~3 就行（层高 4、窗高 2 时正好留 1 格墙），不要照抄 pitch_v，` +
      `否则上下两排窗会连成一片；`
  // 细节重心也不同：现代建筑靠"面"（幕墙 / 百叶 / 女儿墙），不靠"件"（阳台 / 烟囱）。
  const extraDetailNote = isModern
    ? `\n- 细节落在**面**上：每层一条 curtain_wall、一段 louver、屋面一个设备间；` +
      `不要写阳台 / 烟囱 / 老虎窗 / 门廊 —— 这些是住宅与古典的构件；`
    : `\n- 至少给两处细节（阳台/雨棚/烟囱），阳台 depth≥2、w≥4，让立面有虚实；`

  const user =
    head +
    (isShip
      ? shipAsk
      : `\n\n请据此设计一个 Minecraft 1.12.2 建筑方案。要求：` +
        `\n- **先判断这是哪一类建筑，再去套它的语汇**：` +
        `住宅/别墅 → 阳台、雨棚、坡顶；` +
        `宫殿/庄园/府邸 → 长横三段式、孟莎屋顶 + 老虎窗、底层拱廊、柱廊 + 山花；` +
        `学校/图书馆 → 对称、竖向长窗、入口门廊、大体量；` +
        `现代/当代（科研楼、办公、美术馆、综合体）→ 上下错位的盒子 + 悬挑 + 通高幕墙 + 竖百叶，平顶收女儿墙；` +
        `中式/日式 → 大出檐、举折、台基、木构色。分类错了，细节再精致也不像。` +
        `\n- **三类语汇不能混用**：给现代玻璃盒子加孟莎屋顶和老虎窗，` +
        `会立刻变成"白石府邸"；给砖石校舍贴竖百叶，会变成一栋假幕墙。` +
        `\n- band / louver / planter 是**商业立面与现代建筑**的分格手段：` +
        `住宅、学校、宫殿一律不要写（它们的收头是勒脚 + 檐口 + 屋顶）；signbox 只有店面才用；` +
        (isShop
          ? `\n- 体块按"一整片立面"处理，**不要把店面拆成主楼 + 侧翼**；`
          : `\n- 按 massing 信息还原体块（主楼 + 侧翼），不要做成一个方盒子；`) +
        windowNote +
        `\n- **trim 是 wall 的派生色**：明度取 wall 的 70%~80%，色相必须跟着墙走。` +
        `白墙就配浅灰石灰岩（quartz_block / diorite / stone），绝不能配青陶瓦或深灰混凝土——` +
        `线脚占立面三成以上，颜色跑偏会把整栋白楼染成青色。brief 已经算好了，优先照用；` +
        `\n- 但**额外要求里点名的颜色/材质优先于分析结果**：说了"绿琉璃瓦"就必须用` +
        `绿色系屋顶方块（stained_hardened_clay:13 等），说了"米白石材墙"就不能用` +
        `黄色陶瓦；冲突时以额外要求为准；` +
        extraDetailNote +
        `\n- **details 必须包含**：plinth（勒脚）、cornice（檐口）、door（入口门，带 steps 或上方 canopy）；` +
        `如果是最常见的平屋顶住宅，再加一处 balcony 或 curtain_wall；` +
        `\n- 侧立面（east/west）也要排窗，可以比正面稀一半；` +
        `\n- **不要靠加高层高来把楼变高**：层高超过 6 格说明算错了，多出来的高度应该给屋顶。` +
        (showpiece
          ? `\n- **观赏性细节**：窗下外机、花箱、壁灯、屋顶水箱与排气管会由构建端自动补，` +
            `你负责两件事：` +
            `① patches 写 2~4 条材质替换，照着照片里本来就有污渍/色差/换过料的地方写，` +
            `region 取 base（墙脚返潮）/ below_windows（窗下挂污）/ roof（屋面）/ ` +
            `walls（整面墙做旧）/ corners（转角落水管区），` +
            `mat 写 "-0.2" 这种明度偏移（构建端会挑同色系的另一种方块），cover 0.1~0.5；` +
            `② 屋面留一段平的、立面上留几处没有窗的实墙，小件才有地方落。`
          : '') +
        (isShop ? shopAsk : '') +
        (isModern ? modernAsk : '') +
        craftNote + completeNote + aspectNote + wideNote + sizeNote +
        (BY_LEVEL[detail] || ''))

  // 这一轮方案一共花了多少 token（含看图判读、反复修正、视觉复核）
  let usage: TokenUsage = { prompt: 0, completion: 0, total: 0 }
  const count = (u: TokenUsage) => { usage = addUsage(usage, u) }
  const messages: { role: string; content: string }[] = [
    { role: 'system', content: `${rules}\n\n${SCHEMA}` },
    { role: 'user', content: user },
  ]
  // 细节多了以后蓝图会变长，1600 token 会被截断成半个 JSON
  let raw = await chat(messages, {
    temperature: 0.35,
    maxTokens: 12000,
    effort: 'low',
    onUsage: count,
  })
  let spec = parseSpec(raw)

  if (!spec) {
    // 截断 / 多逗号 / 带解释都会走到这里：让它只补一份干净的 JSON 再来一次
    raw = await chat(
      [
        ...messages,
        { role: 'assistant', content: raw.slice(0, 1500) },
        {
          role: 'user',
          content:
            '上面这段不是合法 JSON（可能被截断或有多余符号）。请重新输出完整、合法的 JSON 对象，不要任何解释与代码块标记。',
        },
      ],
      { temperature: 0.1, maxTokens: 12000, effort: 'low', onUsage: count },
    )
    spec = parseSpec(raw)
  }
  if (!spec) {
    // 服务端日志留一份原文，否则前端只看到"不是合法 JSON"，根本没法查。
    // 同时落盘：日志会被刷掉，而且用户看不懂终端。
    console.error('[plan] 蓝图解析失败，原文前 600 字：', String(raw).slice(0, 600))
    try {
      fs.writeFileSync(
        path.join(projectDir(id), 'output', '_plan_fail.txt'),
        String(raw || '').slice(0, 20000),
        'utf-8',
      )
    } catch {
      /* 目录不存在就只留日志 */
    }
    throw createError({
      statusCode: 502,
      statusMessage: `AI 返回的蓝图不是合法 JSON（常见原因：输出被 max_tokens 截断）。原始返回已存 output/_plan_fail.txt，前 120 字：${raw.slice(0, 120)}`,
    })
  }

  // ---- 尺寸：只算一个"请求宽度"，高度交给层数约束，绝不分别拉伸 ----
  const meters: number[] | null = Array.isArray(spec?.building?.meters)
    ? (spec.building.meters as any[]).map((v) => Number(v) || 0)
    : null
  const goodMeters =
    !!meters && meters.length === 3 && meters[0] > 1 && meters[1] > 1 && meters[2] > 1
  const base: number[] = goodMeters ? meters! : [14, 17, 11]

  const FLOOR_M = 3.4
  const hLow = floorsN * 2.2
  const hHigh = floorsN * 6.0
  let fixedMeters = false
  let [mw, mh, md] = base

  // ---- 视觉模型数出来的层数/开间，比任何像素统计都可靠 ----
  // 开间数 × 3.6 米 ≈ 立面总宽，层数 × 4.2 米 + 屋顶 ≈ 总高。
  // 这是"绝对尺寸"唯一的靠谱来源：纯文本模型只能照着体块反推，
  // 一栋 60 米宽的宫殿会被它估成 16 米，然后整个模型缩水一半。
  const vBays = visionBays
  const vFloors = visionFloors
  const trustVision = !isShip && vBays >= 4 && vFloors >= 1
  if (trustVision) {
    mw = Math.round(vBays * 3.6 * 10) / 10
    const roofRatio = Math.max(0, Math.min(0.5, Number(reading?.roof?.height_ratio) || 0.15))
    mh = Math.round(vFloors * 4.2 / Math.max(0.55, 1 - roofRatio) * 10) / 10
    fixedMeters = true
  } else if (!isShip && (mh < hLow || mh > hHigh)) {
    mh = Math.round(floorsN * FLOOR_M * 10) / 10
    fixedMeters = true
  }
  // 宽度的交叉校验：视觉模型给了开间就以它为准，否则才用照片宽高比。
  // 照片宽高比是斜俯视量出来的，系统性偏扁，只配当"离谱检测"。
  // 舰船不适用：照片里船是斜的，宽高比量的是投影，跟船宽没关系。
  if (!isShip && aspect > 0 && mh > 0) {
    const wantW = mh * aspect
    // 视觉模型数开间经常少数（一栋 7 开间的楼数成 4 个），
    // 按开间 × 3.6 米算出来的宽度会只有实际的一半，模型直接缩水。
    // 所以"比照片比例窄太多"这条校正对视觉结果也要跑；
    // 只有"比照片宽太多"才留给 !trustVision —— 那是开间数多了。
    if (mw < wantW * 0.7 || (!trustVision && mw > wantW * 1.45)) {
      mw = Math.round(wantW * 10) / 10
      fixedMeters = true
    }
  }
  if (isShip) {
    // 船的三个数：[船宽, 全高, 船长]。船长是主轴，太短就不是船了；
    // 长宽比压在 6~10（S2），这是"像不像船"的第一道门槛。
    mw = Math.max(4, Math.min(mw, md / 6)) // 船宽由长宽比反推，比 AI 估的稳
    if (md < 24) md = Math.max(24, mw * 6)
    if (md / Math.max(mw, 1) > 12) mw = Math.round(md / 10)
    mh = Math.max(6, mh)
  } else {
    // 店面是"薄片"建筑：进深给到宽度的 40%~55% 就够。给到 24 格时，
    // 等轴测里看到的是一整片灰色屋顶，正面全被挡住。
    const dRatio = isShop ? 0.5 : 0.6
    const dMax = isShop ? 0.58 : 1.0
    md = Math.round(Math.min(mw * dMax, Math.max(mw * dRatio, md || mw * 0.8)) * 10) / 10
  }
  const used: number[] = [mw, mh, md]

  // 只给**一个**长度。构建端会等比缩放，高度跟着它走；
  // 以前同时给宽度和高度，等于让构建端对模型做非等比变形 —— 一栋 6:1 的
  // 长横宫殿被拉成 1.3:1 的方盒子，用户看到的就是"大宫殿还是大房子"。
  // 舰船的主轴是船长（米 × 比例尺），不是船宽。
  const mainMeters = isShip ? used[2] : used[0]
  const rawW = Math.max(isShip ? 40 : 16, Math.round(mainMeters * scale))
  // 补全的宽度**不在这里乘**：蓝图已经是按"整栋"设计的，再乘一遍等于
  // 把每个开间拉宽一倍（补全变成放大）。比例只用来写要求（见 completeNote）。
  // 显式宽度优先：给了就直接用它（仍然夹在 24~132 之间），不再看 AI 估的米数。
  const targetW = forcedW > 0 ? Math.min(132, Math.max(24, forcedW)) : Math.min(132, rawW)
  // 高度只作上限参考（层数 × 6 格 + 屋顶），不再用于缩放
  // 高度上限不能只按"层数 × 层高"算：三层玻璃楼在照片里 1.8 宽高比，
  // 48 格宽就该有 26 格高，而 floors × 4.6 + 4 只给到 18 ——
  // 上限一低，构建端就把蓝图按比例压扁，出来永远是"扁长盒子"。
  const aspectH = aspect > 0.3 ? targetW / aspect : 0
  const targetH = isShip
    ? Math.round(used[1] * scale * 1.6 + 8)  // 桅杆/旗杆要留余量
    : Math.max(Math.round(floorsN * 4.6 + 4), Math.round(aspectH * 1.15))

  const applySize = (s: any) => {
    s.detail = detail
    s.complexity = complexity
    // 观赏性细节是显式开关：detail=4 时构建端会再跑一遍 ornament.py
    // （小件堆叠 + 材质替换）。AI 没写 patches 也能跑，会用兜底那几条。
    s.ornament = showpiece
    s.target_width = targetW
    s.target_height = targetH
    // 比例尺必须写进蓝图：构建端拿它算"一层楼允许多高"（cap = max(6, scale×3.3)），
    // 也拿它把米数换成格数。漏了这一行，界面上把比例尺拉到 8 也白拉 ——
    // loads() 会当成默认的 2 格/米，132 格的目标宽度被当成 78 格建出来。
    // 用户给了总宽时，比例尺要跟着"隐含分辨率"走：41 米的立面做成 112 格，
    // 就是 2.7 格/米。不抬 scale 的话，每层的高度上限（scale × 3.3）会把
    // 缩放系数 k 压回去 —— 同一份蓝图实测：scale=2 只出 75 格，
    // 抬到 6.2 才真的到 114 格。宽度旋钮不联动它就是个假按钮。
    s.scale = forcedW > 0 ? Math.min(12, Math.max(scale, targetW / Math.max(used[0], 1))) : scale
    s.seed = seed
    // 主体类型必须跟着蓝图走：构建端靠它跳过勒脚/檐口，
    // 体检端靠它换一套量测标准。丢了它就又变回"长条写字楼"。
    s.subject = isShip ? 'ship' : String((reading as any)?.subject || 'building')
    s.building = {
      type: String(s?.building?.type || (isShip ? '舰船' : '建筑')),
      meters: used,
      ...(fixedMeters && goodMeters ? { raw: meters } : {}),
    }
    // 补全记录：这张照片被裁过、按多少倍补的，写进清单便于事后核对
    if (cutSides.length) {
      s.complete = { cut: cutSides, ratio: compRatio, bays: compBays || 0 }
    }
    if (aspect > 0) s.aspect = aspect
    return s
  }
  applySize(spec)
  // 白石头建筑别被写成灰盒子：照片量出来是近白石材、AI 却给了混凝土灰时，改回来
  const palFixed = fixWallPalette(spec, (brief as any)?.palette, extra)
  if (palFixed.length) console.log('[plan] 配色兜底：', palFixed.join('；'))
  // 形体兜底：视觉判读到"六边形平面 / 朝外又朝前地斜"，但蓝图里一个字段都没写时，
  // 在这里按几何规则补上。不补的话，AI 写不写是抛硬币 —— 用户看到的
  // "更新完和上一版一样"就是这么来的。
  const enforced: string[] = [...enforceTilt(spec, reading), ...enforceShape(spec, reading)]
  if (enforced.length) console.log('[plan] 形体兜底：', enforced.join('；'))

  // ---- 体检 -> 修正 的闭环 ----
  // 一轮定稿的问题是"没人看结果"：AI 写完就走，构建器照章办事，
  // 像不像只能等用户看图。这里把模型构建回来量一遍，把偏差喂回去，
  // 让 AI 只改该改的地方 —— 这就是"AI 跑蓝图"，蓝图不再是死剧本。
  const cand = path.join(projectDir(id), '_candidate.json')
  const isoPath = path.join(projectDir(id), '_preview_iso.png')
  const cmpPath = path.join(projectDir(id), '_preview_cmp.jpg')
  const rounds: { round: number; score: number; issues: string[] }[] = []
  let bestSpec = JSON.parse(JSON.stringify(spec))
  let bestScore = -1
  let lastVision: any = null
  /** 这一轮有没有真的看过图（分数高时曾经跳过，结果"有眼睛却没用"） */
  let visionChecked = false
  /** 主体就不是建筑、视觉复核也很低：再改也没用，停下来把话说清楚 */
  let hopeless = ''
  // 轮数按第一轮的分数定：第一轮就不错的（≥60）再改一轮就够，
  // 三轮实测常常是"第三轮没长进"或者"越改越差"，白花一分钟。
  const MAX_ROUNDS_HARD = 3
  let maxRounds = MAX_ROUNDS_HARD
  const PASS = 84
  /**
   * 时间预算。一轮 = 本地构建+体检（几秒）+ 视觉复核（20~40 秒）+ 改蓝图（20~40 秒），
   * 三轮就是两三分钟 —— 用户看到的是一个不动的"AI 思考中"，很容易以为卡死。
   * 到点就收：把目前最好的一版交出去，比继续烧第四轮更有用。
   */
  const BUDGET_MS = 150_000
  const T0 = Date.now()

  const measure = (s: any) => {
    fs.writeFileSync(cand, JSON.stringify(s, null, 2), 'utf-8')
    try {
      return runCoreJson<any>(
        ['--critique', '--spec', cand, '-i', photo, '--width', width, '--critique-iso', isoPath],
        240_000,
      )
    } finally {
      try {
        fs.unlinkSync(cand)
      } catch {
        /* 临时文件删不掉不影响结果 */
      }
    }
  }

  try {
    for (let r = 0; r < maxRounds; r++) {
      // 留一轮的余量（约 45 秒）：剩下不到一轮的时间就别再开了
      if (r > 0 && Date.now() - T0 > BUDGET_MS - 45_000) {
        rounds.push({
          round: r + 1,
          score: bestScore,
          issues: ['[停] 已到时间预算（约 2.5 分钟），保留目前最好的一版'],
        })
        break
      }
      const prevBest = bestScore
      const kr = measure(spec)
      let sc = Number(kr?.score ?? 0)
      const top = (kr?.issues || []).slice(0, 6)

      // ---- 第二双眼睛：视觉模型直接看"原图 vs 模型" ----
      // 本地体检只会算比例和密度，算不出"这个孟莎屋顶看起来像一块压平的板"。
      // **每一轮都要看**：看了图的分数是"量测 55% + 观感 45%"，没看的是纯量测，
      // 两者不可比 —— 之前只在低分时才看，结果"最优"总滑向没看过图、分数虚高的那一轮。
      let vIssues: { issue: string; fix: string }[] = []
      let looked = false
      if (vprobe?.ok) {
        // 把蓝图里"看一眼就能确定"的事实告诉视觉模型：等轴测图里平顶
        // 和坡顶很容易被看混，不给这一句，它会连着三轮说"屋顶做成了坡顶"，
        // 把 AI 逼着去改一个本来就对的字段。
        const roofKind = String((spec?.roof || {}).type || 'flat')
        const facts =
          `\n\n补充事实（以蓝图为准，不要推翻）：本次模型的 roof.type = ${roofKind}` +
          (roofKind === 'flat' ? '（平顶 + 女儿墙，图上那一大片屋面是平的）' : '') +
          `；模型总尺寸 ${JSON.stringify(kr?.size || [])}（宽×高×深，单位格）。`
        const vd = await diffRender(photoForVision, String(kr?.iso || isoPath), cmpPath, facts)
        if (vd) {
          lastVision = vd
          visionChecked = true
          looked = true
          vIssues = vd.issues
          // 两个信号加权：本地量的是硬指标，视觉看的是整体观感，都给一半
          sc = Math.round(sc * 0.55 + vd.score * 0.45)
          // 主体就不对（车/飞机/人物），构建器没有那套语汇，再改几轮也只会
          // 重新摆一个方盒子 —— 与其烧钱不如停下来说清楚。
          // 舰船不算：它已经有专用语法（尖艏/甲板/上层建筑/栏杆），可以继续迭代。
          const offSubject = !!reading && !isShip && String(reading.subject || 'building') !== 'building'
          if (offSubject && vd.score < 45) {
            hopeless = `这张图的主体是「${reading?.type || reading?.subject}」，构建器只有建筑语汇，` +
              `再迭代也只会摆出一个方盒子。要做到像，需要给这个主体单独做一套体块模板。`
          }
        }
      }
      if (vprobe?.ok && !looked) {
        // 这一轮视图渲染失败了：只有量测那一半，压下去别和看过图的高分混排
        sc = Math.round(sc * 0.55)
      }

      rounds.push({
        round: r + 1,
        score: sc,
        issues: [
          ...vIssues.map((i) => `[看] ${i.issue}`),
          ...top.map((i: any) => String(i.issue || '')),
        ].slice(0, 6),
      })
      if (sc > bestScore) {
        bestScore = sc
        bestSpec = JSON.parse(JSON.stringify(spec))
      }
      if (r === 0) maxRounds = sc >= 60 ? 2 : MAX_ROUNDS_HARD
      if (hopeless || sc >= PASS || r === maxRounds - 1) break
      // 改了一轮没长进（<2 分）：再来一轮大概率还是原地打转，或者越改越差
      // （实测有 54 → 39 的，也有连着两轮 44 的）。两种情况都该停。
      if (r > 0 && sc - prevBest < 2) {
        rounds.push({
          round: r + 2,
          score: 0,
          issues: [
            sc < prevBest
              ? '[停] 这一轮比上一轮差，不再继续改，保留最高分那版'
              : '[停] 这一轮几乎没有改善，不再继续，保留最高分那版',
          ],
        })
        break
      }

      // 只把"问题 + 可执行补丁"喂回去，不重发照片分析，省 token
      const issuesText = [
        ...vIssues.map((i, k) => `${k + 1}. [看图] ${i.issue}\n   修法：${i.fix}`),
        ...top.map(
          (i: any, k: number) =>
            `${vIssues.length + k + 1}. [量测] ${i.issue}\n   修法：${i.fix}`,
        ),
      ].join('\n')
      messages.push({ role: 'assistant', content: JSON.stringify(spec) })
      messages.push({
        role: 'user',
        content:
          `上面这份蓝图已经能在本地构建，体检得分 ${sc}/100（满分 100，84 分以上就够像了）。` +
          `\n实测尺寸 ${(kr.size || []).join(' × ')}，宽高比 ${kr.aspect}，照片 ${kr.photo_aspect}。` +
          `\n按重要度排列的问题：\n${issuesText}\n\n` +
          (kr.patches?.length
            ? `机器可执行的补丁（可以直接照抄，也可以换成你自己的改法）：\n` +
              JSON.stringify(kr.patches, null, 1) +
              '\n\n'
            : '') +
          `请**只修改这些问题涉及的地方**，其余保持不变，输出一份完整、合法的 JSON 蓝图。` +
          `\n硬性要求：` +
          (isShip
            ? `\n- 长轴沿 Z、舰艏在 z 最大端，舰艏用 taper 收尖（不是平头）；` +
              `\n- 上层建筑逐层内收，甲板沿船长从船头到船尾依次排布；` +
              `\n- 栏杆（railing）、旗杆（flagpole）必须有；不要写勒脚/檐口/门/阳台；`
            : isModern
            ? `\n- 层高保持 4~6 格，屋面只能是 flat + 女儿墙；**不要**改回 mansard / hip / 老虎窗；` +
              `\n- 体量是**上下错位的盒子**：每个盒子各偏 2~6 格，至少一处探出下层 2~4 格；` +
              `\n- 正面靠 curtain_wall + louver：幕墙铺满面宽、跨整层，百叶盖在同一条 u 上；` +
              `\n- 幕墙 / 百叶要么做对，要么不做，不要半截；`
            : `\n- 不要把楼"拉高"来解决问题：层高保持 3~6 格，多出来的高度给屋顶（孟莎 4~6 格）；` +
              `\n- 体量要拆成主体 + 两翼，主体占 45%~60% 宽；` +
              `\n- 屋顶、拱廊、老虎窗这类构件，要么做对，要么不做，不要半截；`) +
          `\n- 只输出 JSON，不要解释。` +
          `\n\n注意：构建器只用整方块，坡屋顶在等轴测图里天然呈台阶状，` +
          `看图评语里"屋顶像平顶/像厚檐口"这类话，**改 roof 字段是修不掉的**` +
          `（已经是 hip 就不要再改成 hip）。这类只能忽略，把力气花在能改的地方：` +
          `体块拆分、层数、开间与窗列、颜色分区、必需构件的有无。`,
      })
      const next = parseSpec(
        await chat(messages, { temperature: 0.2, maxTokens: 12000, effort: 'low', onUsage: count }),
      )
      if (!next) break
      spec = applySize(next)
      // 每轮改完也要兜一次：AI 重写整份 JSON 时最容易把 shape/lean 写丢
      enforced.push(...enforceTilt(spec, reading), ...enforceShape(spec, reading))
      // AI 可能在修正时把 seed 写丢，每次都补回来
      spec.seed = seed
    }
  } catch (e: any) {
    // 体检失败不该让整个方案失败：至少把第一版还给用户
    if (!rounds.length) {
      rounds.push({ round: 1, score: 0, issues: [`体检未完成：${String(e?.message || e).slice(0, 120)}`] })
    }
  }

  // 预览图只是循环里的中间产物，取到最优版本就清掉
  for (const f of [isoPath, cmpPath, visionPhoto]) {
    try {
      fs.unlinkSync(f)
    } catch {
      /* 没有就跳过 */
    }
  }

  return {
    spec: bestSpec,
    // 语汇分支：舰船 / 商业 / 现代 / 通用建筑。前端据此提示"这是哪一套语法"
    modern: isModern,
    // 形体兜底补了哪些字段（六边形平面 / 双向斜）。界面可以据此提示
    enforced,
    brief,
    // 主体类型：舰船走另一套语法，前端据此提示"这是船，不是楼"
    subject: isShip ? 'ship' : String((reading as any)?.subject || 'building'),
    detail,
    scale,
    target: [targetW, targetH],
    metersFixed: fixedMeters,
    seed,
    score: bestScore,
    rounds,
    // 视觉这一路的产出：判读结果 + 最后一轮的观感评价 + 探测结论
    vision: reading,
    visionDiff: lastVision,
    visionProbe: vprobe,
    visionChecked,
    // 主体与构建器语汇不匹配时的解释（空串表示正常）
    note: hopeless,
    // 这一次方案一共花了多少 token（仓库页要显示）
    usage,
    // 分析过程的快照：仓库页「修改」时把这份原样喂回给 AI，
    // 它才知道"上一次看了什么、改了几轮、为什么改成这样"
    analysis: {
      modern: isModern,
      craft,
      hint: String(body.hint || ''),
      detail,
      scale,
      seed,
      vision: reading,
      rounds,
      score: bestScore,
      note: hopeless,
      at: new Date().toISOString(),
    },
  }
})
