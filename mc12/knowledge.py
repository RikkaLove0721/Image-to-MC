"""MC 建筑设计规则：给 AI 当"常识"，也用来校验它写的 spec。

AI 生成建筑最容易犯三种错：层高给 2 格（游戏里进不去）、窗比墙还大、
屋顶坡度比房子还高。这些不需要像素分析，是建筑常识 —— 放在这里，
既拼进提示词，也在本地做一次校验，省得让 AI 反复试错浪费 token。

每条规则都对应一个可执行检查，`check_spec()` 会把违反的地方列出来。
"""

from __future__ import annotations

# 层高：3 格太挤、5 格太旷，4 格是 MC 建筑的通例
FLOOR_MIN, FLOOR_MAX, FLOOR_TYP = 3, 5, 4
# 窗：1~4 格宽、2~4 格高；两侧墙垛至少 2 格
WIN_W = (1, 4)
WIN_H = (2, 4)
WIN_GAP_U = 2
WIN_SILL = 1  # 窗台距楼面
# 建筑高宽比：太扁像围墙，太瘦像塔
ASPECT = (0.25, 1.4)

# 材质搭配：MC 建筑靠"一材料一纯色"，主材 + 深色线脚 + 第三种点缀
PAIRINGS = [
    ("sandstone", "concrete:7", "statement"),   # 米石墙 + 深灰线脚
    ("quartz_block", "dark_oak_planks", ""),    # 白墙 + 深木
    ("concrete:0", "concrete:7", ""),           # 白混凝土 + 深灰
    ("brick_block", "stonebrick", ""),          # 红砖 + 石砖
    ("hardened_clay", "dark_oak_planks", ""),   # 陶瓦色 + 深木
]

RULES_TEXT = """MC 建筑设计常识（必须遵守）
1. 层高取 3~5 格，常用 4。低于 3 进不去，高于 5 显空旷。
2. 窗宽 1~4 格、窗高 2~4 格，窗顶不要贴到楼板，窗台至少留 1 格。
3. 窗与窗之间至少留 2 格墙垛；上下两层窗要在同一套列网格上对齐。
4. 一面墙只用一种主材，线脚/檐口用深一档的材料，最多再加一种点缀。
   不要一面墙刷三四种颜色。
5. 平顶要收边：女儿墙 1~2 格。坡屋顶高度不超过 min(宽, 深) 的一半。
6. 体块要有主次：主体占 55%~75% 的长度，侧翼低 2~6 格并向后缩 1~4 格。
7. 大体块要有虚实对比：玻璃幕墙、阳台、雨棚、门廊至少做两处，
   不要整面都是实墙。
8. 总高宽比保持在 0.25~1.4 之间，避免"围墙"和"塔"。
9. 窗阵要在立面上铺开：列数由可用宽度推算 ——
   cols ≈ (墙面宽 − 2×边距 + 间距) ÷ (窗宽 + 间距)，取整，
   覆盖立面 55%~90%。窗全挤在一侧、另一边留一大片实墙是最常见的败笔。
10. 细节构件要成组：阳台至少 2 格进深、带 U 形栏杆；雨棚要压在门的正上方。

细节规定（别墅、住宅、公共建筑都要做到）
11. 必须有入口：门宽 2~4 格、高 3 格，四周有门框，门前铺 1~3 格台阶或压一个雨棚。
    没有门的建筑只能算"立面"，不能算"房子"。
12. 必须有勒脚：贴地一圈（高 1~2 格）换成深一档材料，把楼和地面分开。
13. 必须有檐口：墙顶一圈用深色材料收边；平顶再把女儿墙压顶，
    否则顶上是一道生硬的切口。
14. 现代建筑允许一整片玻璃幕墙：宽 6~14 格、高跨 2~3 层，每 2~4 格一道竖梃。
    有幕墙的一层不要再排小窗。
15. 侧立面和北立面同样要开窗，只是可以比正面稀。只做正面的模型一转就穿帮。
16. 材质总数不超过 4 种：主墙 + 深色线脚 + 玻璃 + 一种点缀（木/金属）。
17. 立面上每 8~12 格要出现一次变化：腰线、墙垛、凹进、材质切换，
    连续十几个格子的纯平面在游戏里非常扎眼。

比例与古典构件（照片是塔楼、别墅时最要紧）
18. **先定比例再定尺寸**：宽 ÷ 高 要跟着照片走（误差 ±15%）。
    竖向的楼先按层数定高，再按比例定宽；为了凑"预设宽度"把楼拉成平房，
    是模型"不像"的头号原因。
19. 古典立面（柱式、栏杆、山花、门廊）必须用 `column` / `portico` 表达，
    不能用一排阳台代替柱廊 —— 柱子的竖向线条就是这类建筑的识别特征。
20. 门厅、楼梯间、整层挑空用**竖向长窗**（h > w，最高可取 4~6），
    普通房间窗宽 2~4；全楼都是等高横窗会让立面变成办公楼。
21. 楼层 ≥3 的塔楼，顶部要有收束：檐口 + 女儿墙，必要时加拱窗或山花。
22. **线脚是墙的派生色，不是另一种颜色**：trim 的明度取 wall 的 70%~80%，
    色相必须跟着墙走（白墙配浅灰石灰岩，不能配青陶瓦 / 深灰混凝土）。
    线脚（勒脚 + 腰线 + 檐口 + 柱）占立面 20%~35%，颜色跑偏会把整栋楼染色 ——
    "照片明明是全白的，模型却是青的"就是这么来的。
23. 古典建筑的柱、栏杆、线脚一律用**石材**（quartz_block / sandstone / stonebrick），
    不要用深色木板；石栏杆写 `"rail": "quartz_block"`，柱子写 `"mat": "trim"`。
    深木色只适合现代/乡野风格。

估算真实尺寸（building.meters）时用可数的参照物，不要凭感觉
24. **先数层数**：数立面有几排窗带（含被门/幕墙占掉的那层），层数是最可靠的信息。
25. 层高按用途取：住宅 / 办公 3.2 米，教学楼 / 图书馆 3.8 米，教堂 / 大殿 6 米以上。
    总高 = 层数 × 层高 + 屋顶（平顶 0.6 米，坡顶按坡度另加 1~3 米）。
26. **横向用门窗反推**：门宽约 1 米、窗宽 1.2~1.8 米、窗间距 0.8~1.5 米。
    数一数立面上有几扇窗，乘以节距，就是立面总宽的近似值。
27. 估算完要自查：三层住宅大约 9~12 米高，联排 6~9 米，教学楼 12~15 米。
    算出三层楼 30 米高、或者 40 米宽 5 米高的，一定是估错了。

大型建筑（宫殿、庄园、府邸、教堂、车站，一眼看去"很长很低"的那种）
28. **先认体量，再认细节**。这类建筑的第一识别特征是"横向很长、纵向很矮"，
    宽高比常在 3~7 之间。把它做成一栋三层的方楼，无论细节多好都不像。
    判断方法：数一数正面有几开间（几扇窗），再数几层 —— 开间数 ÷ 层数 > 4
    就属于长横体量。
29. 长横体量要拆成**三段**：中央主体（占 40%~55% 宽、略高、向前凸 2~4 格）
    + 左右两翼（低 2~4 格，可向后缩 2~4 格）。三段式是这类建筑的基本语法，
    一个长方体无论多长都读不出"宫殿"。
30. **孟莎屋顶**（"roof": {"type":"mansard","height":4~6}）是法式宫殿/府邸的
    标志：深色、坡度下陡上缓、占立面高度三分之一以上。用平顶做，整栋楼立刻
    变成办公楼。屋面上再加 4~8 个 `dormer`（老虎窗），屋顶才不会是一块灰板。
31. 首层用 `arcade`（拱廊）而不是排小窗：每一开间一个拱券、中间立柱，
    高度取 4~5 格。这是"公共建筑"和"住宅"在立面上最大的区别。
32. 入口用 `portico` + `pediment`（柱廊 + 三角山花）压在中央主体上，
    比雨棚 + 双开门气派一个量级。
33. 大型建筑**不要**按住宅的层高往上堆：2~3 层就够，多余的高度要还给
    屋顶（孟莎 4~6 格）和基座（台基 2~3 格）。堆成五层就是宿舍楼。
34. 开间要匀：窗距相等、上下对齐、左右对称。对称是这类建筑的一半。

材料与构件选择（1.12.2 方块库）
35. **玻璃默认用玻璃板（玻璃板 = 染色玻璃板 102）**。现实里窗玻璃几毫米厚，
    社区里做窗也是用板：板只占 1/16 厚、会自动连成整片，贴在洞口上就是窗。
    用整块玻璃会把洞口填死，从外面看是一堵浅蓝色的墙。
    只有**玻璃天花板 / 采光顶 / 玻璃盒子幕墙 / 厚重防护玻璃**才用整块玻璃，
    写法是在那条窗或幕墙上加 `"thick": true`（或 `"glass": "block"`）。
    `curtain_wall`、窗阵、拱廊的玻璃都自动转成板，不用你自己写板名。
36. **绿化带用草方块 + 草丛 + 花**：花坛/种植池顶层铺 `grass`（草方块，
    顶面是草、侧面是土，正好像花坛），上面点 1~2 格 `tallgrass`（草丛），
    再零星插 `yellow_flower`（蒲公英）/ `red_flower`（虞美人）。
    不要用绿色羊毛代替绿化 —— 羊毛是一整块绿，没有草的高度差。
37. **柱子用竖纹石英柱 `quartz_pillar`**（155:2）。竖纹是柱子的视觉标志；
    收边、台面用 `smooth_quartz`（平滑石英块），重点墙面用
    `chiseled_quartz_block`（錾制石英块）。三者同色不同纹，配起来才有层次。
38. **大型灯具用光源方块**：照片里成片的灯箱、灯带、大吊灯，
    用 `lamp` 构件（mat 取 `sea_lantern` 海晶灯 / `glowstone` 萤石 /
    `redstone_lamp` 红石灯）表达，不要用白色混凝土画一块白板 ——
    白天看着像，进游戏晚上不亮，一眼假。
    嵌进立面的灯带写 `"out": 0`（齐平换块），外挂灯箱写 `"out": 1`。
39. 本软件的方块库以 **MC 1.12.2** 为准（数字 ID + metadata）。
    1.13+ 的方块（深板岩、方解石、凝灰岩、玄武岩、铜块、樱花木等）这里没有，
    写了也会兜底成石头。需要别的版本请自行改 `mc12/palette.py` 扩表。
"""

# MC 社区建筑技巧。`craft` 档位越高，越优先用这里的东西，
# 越允许偏离照片的逐像素还原 —— 社区那套"好看"是形式法则，不是采样结果。
CRAFT_TEXT = """MC 社区建筑技巧（网友建筑里反复出现的"好看"套路）

K1. **三色原则**：一面墙最多三种颜色 —— 主材 + 深一档的线脚 + 一种点缀。
    颜色一多就"脏"，这是新手和老手最明显的分界。
K2. **框景**：每个开口（窗/门/拱）外面包一圈深一档的边框，1 格即可。
    没有框的洞口像被挖掉的洞，有框才像"做出来的"。
K3. **收分**：楼层越高越往里收 1 格，或者顶部做一圈挑檐。
    直上直下的盒子在 MC 里像火柴盒。
K4. **腰线分段**：每 2~3 层压一道 1 格深的腰线，把立面切成横向的段。
    这是"楼"和"盒子"的区别。
K5. **不要对称到底**：主体对称，但入口、树、灯、台阶偏移 1~3 格，
    完全对称的模型看着像示意图。
K6. **凹进比凸出好用**：窗、阳台往里凹 1 格，会自然产生阴影，
    比往外挑一堆方块干净得多。
K7. **屋顶要有"面"**：平顶做女儿墙 + 檐口 + 屋面材质区分；坡顶做檐口挑 1 格。
    屋顶是一整块同色的话，从等轴测看就是一个灰板。
K8. **地面要收边**：建筑落地处铺一圈勒脚或台阶，不要让它直接"切"在地面上。
K9. **点缀成组**：花池、灯、树成组出现（2~3 个一组），单个散布像噪点。
K10. **材质要"顺色"**：木配石、石配混凝土、陶瓦配深木。
    亮色的羊毛贴在石材旁边会立刻变成"贴图错误"。
"""


def _material_name(blk) -> str:
    return f"{blk.name_en}:{blk.meta}" if blk.meta else blk.name_en


# --------------------------------------------------------------------------
# 舰船：和建筑是**两套语法**，不能共用一份规则
# --------------------------------------------------------------------------
# 建筑的第一识别特征是"宽、矮、立面对称、有屋顶"；舰船正好相反：
# 细长、有尖艏、上层建筑偏后、顶上是甲板。把建筑规则套到船上，
# 得到的就是一栋长条形写字楼 —— 这不是模型不够细，是语法错了。

SHIP_RULES_TEXT = """舰船建模常识（军舰 / 货轮 / 客轮 / 航母通用；与建筑规则完全不同）

S1. **朝向**：船长轴沿 Z 轴（南北），舰艏朝南（z 最大端）。
    这样"正面视图"看到的就是迎面驶来的船头，而不是一堵船舷。
    volumes 里 d 是船长、w 是船宽、h 是高度。
S2. **长宽比 6~10**：80 格长的船，宽 10~14 格。做成 3:1 就是一条驳船；
    做成 2:1 是澡盆。这是"像不像船"的第一道门槛。
S3. **舰艏必须是楔形收窄，不是平头**。用体块的 taper 字段：
    {"x":0,"y":0,"z":0,"w":14,"h":5,"d":80,
     "taper":{"axis":"z","end":"max","to":2,"len":26}}
    axis 是收窄方向，end 是要收尖的那一端（max = 船头），to 是末端宽度（1~3），
    len 是收窄段长度（船长的 1/4~1/3）。平头的模型一眼就不是船。
S4. **船体分两层**：主船体（h 4~6，舷侧垂直）+ 甲板（h 1，深一档材料）。
    甲板也要带同样的 taper，否则船头会露出一截"没有甲板的船体"。
    舷侧想内倾就再叠一层窄 1~2 格的体块，不要用屋顶收坡的办法。
S5. **上层建筑在船中偏后**，逐层内倾：每上一层宽度和进深各缩 1~2 格，
    3~4 段堆成台阶状（这才是隐身舰的封闭桅杆造型）。
    舰桥正面是一条横向玻璃带，不要排一排小方窗 —— 军舰立面几乎没有窗。
S6. **桅杆/雷达**用单格细柱（w 1~2、d 1~2、h 6~10）压在上层建筑顶，
    不要用 4 格见方的方块，那是烟囱。
S7. **甲板边缘要有栏杆**：{"type":"railing","volume":甲板体块,"mat":"iron_bars"}，
    上层建筑的平台同样加一圈。栏杆是"这是船"最廉价也最有效的线索。
S8. **舰尾或桅顶要有旗杆**：
    {"type":"flagpole","volume":甲板体块,"u":6,"v":3,"h":10,"side":"east","flag":"wool:14"}
    旗用羊毛（wool:14 红 / wool:11 蓝 / wool:15 黑）。
S9. **禁止建筑构件**：不要写 plinth（勒脚）、cornice（檐口）、door（门）、
    balcony（阳台）、arcade、portico、pediment、dormer。
    这些装在船上，整艘船立刻变回写字楼。
S10. 屋顶只能写 {"type":"flat","parapet":0}，顶面就是甲板。
S11. 窗要少而长：只在舰桥和上层建筑开 1~2 条横向窗带，
    不要给船体铺满窗户。
S12. 结构件（主炮、垂发、雷达阵面、烟囱）用小的长方体体块表示，
    尺寸 2~4 格、贴在甲板或上层建筑上，不要做成立方体孤岛。
"""


ORNAMENT_TEXT = """观赏性细节（detail=4，MC 社区做法）

N1. 建筑"像不像"由**不均质**决定，不是配色对决定的。一整面完全均匀的墙，
    在游戏里反而像贴图，不像盖出来的。
N2. 做旧用"同材质深浅两档"，不要换色系：白墙压暗是浅灰石材，不是蓝灰黏土。
    在 patches 里把 mat 写成 "-0.25"（暗一档）或 "+0.08"（亮一档）即可，
    构建端会自己挑同族、同色相的方块。
N3. 小件成组、不等距、**只在一侧**：设备箱 1~2 格高、天线 1×1 竖 2~4 格、
    救生筒白色竖 2 格、屋顶水箱 2×2×2、排气管顶一根铁栏杆。
    左右对称地摆，模型立刻变玩具。
N4. 墙面外挂只做在一部分窗上：窗下外机（铁块 + 深灰）、花箱（泥土 + 树叶）、
    檐下埋一格萤石当壁灯。留出空白比铺满更像真的。
N5. 分块用微差：甲板 / 屋面每 6~10 格换一块 -0.08~-0.12 的料，
    不要逐格换（那是噪点）。
N6. **一定要照照片来**：原图里哪块本来就脏、哪排窗下杂物多，就只在那里做。
    均匀铺满全楼等于没有还原度。
N7. 舰船的甲板设备沿船长成组布置，靠近舰桥和机库口更密，舰艏甲板留空。
"""


COMMERCIAL_TEXT = """商业立面常识（店面 / 餐厅 / 商铺 / 展厅 / 玻璃盒子；这是另一套语汇）

C1. 商业立面是**横三段**，从下往上一格都不能少：
    深色勒脚（1~2 格，石材/深灰）→ 通高玻璃橱窗（4~8 格）→ 顶部招牌带（2~3 格，木纹或彩色铝板）。
    少任何一段，它就退化成"一栋小房子"。
C2. 玻璃必须占立面的 50%~75%，用 curtain_wall 整片铺：
    mullion 3~4 格一根竖梃、transom 3~4 格一道横梁。
    只画竖梃，整片玻璃会像一块蓝招牌；一格分格都不做，就像一大块塑料。
C3. 入口在**中轴或黄金分割点**，双开门 w 4~6、h 4~5，门头用 band 或 canopy 出 1 格，
    门前 2~3 格台阶或一块铺装。入口是唯一允许"体量凹进去"的地方。
C4. 招牌（signbox）是这类建筑最强的识别信号，比材质准不准重要得多：
    竖向招牌柱 w 5~6、**v 取 0、h 取到招牌带（h ≥ 8）**、out 1、mat 用品牌色；
    横向招牌带用 band 盖满立面顶部。logo 可选 m / arch / star / ring / bars。
    注意 **h 是竖直高度**：h 写成 2 就是一条横招牌，不是柱子 —— 这是最容易写错的字段。
C5. 转角、设备遮挡、幕墙分隔用 louver（竖向格栅，step 2、out 1）。
    一排竖条比一块实心板"透气"，这是现代立面看起来轻的根本原因。
C6. 花池 planter 贴地 2~4 格，种树叶；**不要摆满整条街**，一段就够。
C7. 商业立面几乎都是**左右对称**的：中轴入口、两侧开间数相同。
    照片被裁掉一边时必须按对称补全成完整一栋 —— 只建可见的那段，产出永远是"半栋楼"。
C8. 招牌最多两处。铺满整条街就是广告牌，不是建筑。
C9. **不要**把住宅语汇搬过来：坡屋顶、阳台、孟莎顶、老虎窗、拱廊、柱廊、烟囱。
    商业立面是平的，收头靠檐口带和女儿墙。
"""


def rules_text(subject: str = "", ornament: bool = False, craft: int = 1) -> str:
    """按主体类型给规则。舰船不套建筑那 34 条，否则 AI 一定做回一栋楼。

    subject 也可以传 "shop"/"commercial"/"storefront"：商业立面不是另一种主体，
    而是建筑里的一支，所以**追加**一段商业常识，而不是替换掉通用规则。

    craft = 0/1/2（低/中/高）「游戏建筑技巧性」。中档只在末尾提一句，
    高档把社区那 10 条整段塞进去，并且**明确允许偏离照片的逐像素还原** ——
    这是"还原度"和"好玩"之间唯一可调的旋钮。
    """
    s = str(subject or "").lower()
    ship = s in ("ship", "vessel", "warship", "boat")
    base = SHIP_RULES_TEXT if ship else RULES_TEXT
    if not ship and any(k in s for k in ("shop", "commercial", "store", "retail")):
        base = f"{base}\n\n{COMMERCIAL_TEXT}"
    if ornament:
        base = f"{base}\n\n{ORNAMENT_TEXT}"
    if int(craft) >= 2:
        base = f"{base}\n\n{CRAFT_TEXT}"
    elif int(craft) == 1:
        base = (
            f"{base}\n\n（风格取向：中。在还原照片的前提下，套用社区通用做法 ——"
            "三色原则、开口包框、腰线分段、地面收勒脚。）"
        )
    return base


def _check_ship(raw: dict) -> dict:
    """舰船专项检查：只看"像不像船"，不套建筑的开间/层高/勒脚。"""
    errors: list[str] = []
    warnings: list[str] = []
    _btype = str((raw.get("building") or {}).get("type") or "")
    _shop = any(k in _btype for k in SHOP_WORDS)
    vols = raw.get("volumes") or []
    if not vols:
        return {"ok": False, "errors": ["volumes 为空：至少要有一个体块"], "warnings": []}

    lens = [int(v.get("d", 0)) for v in vols]
    wid = [int(v.get("w", 0)) for v in vols]
    length = max(int(v.get("z", 0)) + int(v.get("d", 0)) for v in vols) - min(
        int(v.get("z", 0)) for v in vols
    )
    beam = max(int(v.get("x", 0)) + int(v.get("w", 0)) for v in vols) - min(
        int(v.get("x", 0)) for v in vols
    )
    if beam > 0 and length > 0:
        ratio = length / beam
        if ratio < 4:
            errors.append(
                f"船长 {length} / 船宽 {beam} = {ratio:.1f}，太粗短；船的长宽比要 6~10"
            )
        elif ratio > 14:
            warnings.append(f"长宽比 {ratio:.1f} 过于细长，像一根杆子")
    # 长轴必须沿 Z：否则正面看到的是船舷，模型再好也读不出"船头对着你"
    if max(wid) > max(lens) * 0.9:
        errors.append(
            "船长轴没有沿 Z 轴：请把船长写进 d（南北方向），舰艏放在 z 最大端，"
            "这样正面视图才是船头迎面"
        )

    has_taper = any(
        isinstance(v.get("taper"), dict) and (v.get("taper") or {}).get("axis") in ("x", "z")
        for v in vols
    )
    if not has_taper:
        errors.append(
            "没有楔形舰艏：给主船体加 taper（axis=z、end=max、to=1~3、len=船长的 1/4~1/3），"
            "平头的模型不是船"
        )

    kinds = {str(d.get("type", "")) for d in (raw.get("details") or [])}
    if "railing" not in kinds:
        warnings.append("甲板边缘没有栏杆（railing）：光秃秃的甲板边会被读成楼顶")
    if "flagpole" not in kinds:
        warnings.append("没有旗杆（flagpole）：舰尾或桅顶立一根，识别度很高")
    bad = kinds & {"plinth", "cornice", "balcony", "arcade", "portico", "pediment", "dormer", "door"}
    if bad:
        warnings.append(f"这些是建筑构件，装在船上会变回写字楼：{'、'.join(sorted(bad))}")

    roof = raw.get("roof") or {}
    if str(roof.get("type", "flat")) != "flat":
        warnings.append("船的顶面是甲板，roof.type 只能是 flat")

    wins = raw.get("windows") or []
    win_cells = sum(
        int(w.get("cols", 1)) * int(w.get("rows", 1)) for w in wins
    )
    if win_cells > 40:
        warnings.append(f"窗阵共 {win_cells} 扇，军舰立面几乎全是钢板，只在舰桥开 1~2 条窗带")

    warnings.extend(_check_ornament(raw))
    return {"ok": not errors, "errors": errors, "warnings": warnings}


REGION_OK = {
    "hull_lower", "hull_side", "deck", "superstructure",
    "base", "roof", "below_windows", "walls", "corners",
}


def _check_ornament(raw: dict) -> list[str]:
    """观赏性细节（detail=4）的检查：小件与材质替换写得对不对。

    这一层的容错很高（区域名不认识就跳过，方块名不认识就不替换），
    所以这里全部给 warning —— 不值得为几块装饰把小件整体拒掉。
    """
    warns: list[str] = []
    try:
        detail = int(raw.get("detail") or 1)
    except (TypeError, ValueError):
        detail = 1
    if detail < 1 or detail > 4:
        warns.append("detail 只能取 1~4（4 = 观赏性细节）")
    orn = raw.get("ornament") is True or detail >= 4
    if not orn:
        return warns
    patches = raw.get("patches")
    if not isinstance(patches, list) or len(patches) < 2:
        warns.append(
            "观赏性细节建议写 2~4 条 patches（材质替换），"
            '例如 {"region":"hull_lower","mat":"-0.28","cover":0.9}；'
            "不写就只能按兜底的「墙脚 / 窗下 / 水线」做旧"
        )
    for i, p in enumerate((patches or [])[:6]):
        if not isinstance(p, dict):
            continue
        region = str(p.get("region") or "")
        if region not in REGION_OK:
            warns.append(
                f"patches[{i}].region「{region}」不认识，可用：{'、'.join(sorted(REGION_OK))}"
            )
        mat = str(p.get("mat") or "")
        if mat.startswith(("+", "-")):
            try:
                float(mat)
            except ValueError:
                warns.append(f'patches[{i}].mat「{mat}」要写成 "-0.2" 这样的明度偏移或方块 id')
    return warns


SHOP_WORDS = ("店", "商业", "餐厅", "商铺", "展厅", "快餐", "超市", "银行", "门店", "营业厅")


def _check_commercial(raw: dict, kinds: set[str], roof: dict) -> list[str]:
    """商业立面的专项检查。

    这类建筑的"像不像"跟住宅完全是两套标准：住宅要屋顶、要阳台，
    店面要的是一条通高玻璃、一条招牌带、一处招牌。拿住宅那套去套，
    AI 会认认真真给一家麦当劳加上孟莎屋顶和老虎窗。
    """
    btype = str((raw.get("building") or {}).get("type") or "")
    shop = any(k in btype for k in SHOP_WORDS)
    if not shop:
        # 反过来也要管：住宅/学校写上了商业构件，会变成"贴了张招牌的居民楼"
        borrowed = {"band", "louver", "planter", "signbox"} & kinds
        if borrowed:
            return [
                f"这是「{btype or '非商业建筑'}」，却用了商业立面构件"
                f"（{'、'.join(sorted(borrowed))}）：住宅/学校靠勒脚 + 檐口 + 屋顶收头"
            ]
        return []
    out: list[str] = []
    if "signbox" not in kinds:
        out.append("商业立面没有招牌（signbox）：这是它最强的识别信号，比材质准不准重要")
    if "curtain_wall" not in kinds:
        out.append("没有通高玻璃（curtain_wall）：店面要有 50%~75% 的玻璃，否则像小房子")
    if "band" not in kinds:
        out.append("没有水平饰带（band）：横三段里的招牌带/檐口带是商业立面的骨架")
    if str(roof.get("type", "flat")) != "flat":
        out.append("商业立面是平的，屋顶还给成了坡顶：改回 flat + parapet 1~2")
    bad = {"balcony", "dormer", "arcade", "column", "pediment", "portico"} & kinds
    if bad:
        out.append(f"混进了住宅/古典构件（{'、'.join(sorted(bad))}），商业店面用不上")
    signs = [d for d in (raw.get("details") or []) if str(d.get("type")) == "signbox"]
    if len(signs) > 2:
        out.append(f"招牌有 {len(signs)} 处，最多两处 —— 铺满整条街就是广告牌")
    # 竖向招牌柱写成 h=2 是这一轮最常见的错：AI 把 h 当成"横带的高度"。
    if signs and not any(int(x.get("h", 0) or 0) >= 6 for x in signs):
        out.append(
            "signbox 都太矮（h<6）：竖向招牌柱要 v=0、h≥8 一直通到招牌带，"
            "否则它只是一条贴在墙上的横带，撑不起识别度"
        )

    # 两层店面必须"看得见两层"：一条玻璃带横跨两层高，中间没有楼板线，
    # 远看就是一层的挑高大堂 —— 这是麦当劳这类店最容易做塌的地方。
    details = raw.get("details") or []
    cw = [d for d in details if str(d.get("type")) == "curtain_wall"]
    fl = max([int(v.get("floors") or 0) for v in (raw.get("volumes") or [])] or [0])
    if fl >= 2 and len(cw) < 2:
        out.append(
            f"声明了 {fl} 层，但只有 {len(cw)} 条幕墙：两层店面要**两条**玻璃带，"
            "中间夹一条白色楼板线（band h1、out 1、quartz_block:0），"
            "否则二层看不出来，整栋像一层挑高大堂"
        )
    if len(cw) >= 2:
        # 只在**同一面墙**上比对齐：退台的三楼是另一个体块、另一种开间，
        # 拿它去比二楼的 u/mullion 会永远对不上。
        groups: dict[tuple, list] = {}
        for d in cw:
            key = (int(d.get("volume") or 0), str(d.get("face") or "south"))
            groups.setdefault(key, []).append(d)
        for (vi, face), group in groups.items():
            if len(group) < 2:
                continue
            mull = {int(x.get("mullion", 3) or 3) for x in group}
            us = sorted(int(x.get("u", 0) or 0) for x in group)
            if len(mull) > 1 or us[0] != us[-1]:
                out.append(
                    f"体块 {vi} 的 {face} 面上几条幕墙的 u / mullion 不一致："
                    "竖梃要对齐，上下错开一格在 1 格宽的尺度上像墙面开裂"
                )
    # 三层店面：上面那层通常是**退台**（更小、偏一侧），不是一个等宽的盒子
    vols = raw.get("volumes") or []
    if len(vols) >= 2:
        base = vols[0]
        for i, v in enumerate(vols[1:], start=1):
            if int(v.get("y", 0)) <= int(base.get("y", 0)):
                continue
            same_w = int(v.get("w", 0)) == int(base.get("w", 0))
            same_d = int(v.get("d", 0)) == int(base.get("d", 0))
            same_x = int(v.get("x", 0)) == int(base.get("x", 0))
            same_z = int(v.get("z", 0)) == int(base.get("z", 0))
            if same_w and same_d and same_x and same_z:
                out.append(
                    f"体块 {i} 整个压在体块 0 上、尺寸位置完全一样：这不是退台，"
                    "渲染出来是一堵更高的墙。上面那层要么缩进 2~4 格（x/z 偏移 + w/d 收窄），"
                    "要么干脆不要，只留女儿墙"
                )
    return out


def check_spec(raw: dict) -> dict:
    """检查一份 spec，返回 errors（必须改）与 warnings（最好改）。"""
    subject = str(
        raw.get("subject") or (raw.get("building") or {}).get("subject") or ""
    ).strip().lower()
    if subject in ("ship", "vessel", "warship", "boat"):
        return _check_ship(raw)
    errors: list[str] = []
    warnings: list[str] = []
    _btype = str((raw.get("building") or {}).get("type") or "")
    _shop = any(k in _btype for k in SHOP_WORDS)
    vols = raw.get("volumes") or []
    if not vols:
        errors.append("volumes 为空：至少要有一个体块")
        return {"ok": False, "errors": errors, "warnings": warnings}

    total_w = max(int(v.get("x", 0)) + int(v.get("w", 8)) for v in vols)
    total_h = max(int(v.get("y", 0)) + int(v.get("h", 6)) for v in vols)
    if total_w <= 0 or total_h <= 0:
        errors.append("体块尺寸必须为正")
        return {"ok": False, "errors": errors, "warnings": warnings}

    ratio = total_w / max(total_h, 1)  # 宽 ÷ 高
    photo = float(raw.get("aspect") or 0)
    if photo > 0 and ratio > photo * 1.45:
        warnings.append(
            f"宽高比 {ratio:.2f} 明显比照片的 {photo:.2f} 宽，模型会被拉成平房；"
            "先按层数定高，再按比例定宽"
        )
    if not (0.4 <= ratio <= 3.2):
        warnings.append(f"宽高比 {ratio:.2f} 过于极端，看着像围墙或塔")

    for i, v in enumerate(vols):
        h = int(v.get("h", 0))
        fl = int(v.get("floors", 0))
        if fl:
            step = h / fl
            _top = 8 if _shop else FLOOR_MAX
            if step < FLOOR_MIN:
                errors.append(f"体块 {i}: 层高 {step:.1f} 格太矮（应 ≥{FLOOR_MIN}）")
            elif step > _top:
                warnings.append(f"体块 {i}: 层高 {step:.1f} 格偏高（建议 {FLOOR_TYP}）")
        if min(int(v.get("w", 0)), int(v.get("d", 0))) < 3:
            warnings.append(f"体块 {i}: 平面太窄，做不出房间")

    # 体块之间要有搭接，不能只是贴在一起
    for i in range(len(vols)):
        for j in range(i + 1, len(vols)):
            a, b = vols[i], vols[j]
            ax0, ax1 = int(a.get("x", 0)), int(a.get("x", 0)) + int(a.get("w", 0))
            bx0, bx1 = int(b.get("x", 0)), int(b.get("x", 0)) + int(b.get("w", 0))
            az0, az1 = int(a.get("z", 0)), int(a.get("z", 0)) + int(a.get("d", 0))
            bz0, bz1 = int(b.get("z", 0)), int(b.get("z", 0)) + int(b.get("d", 0))
            ox = min(ax1, bx1) - max(ax0, bx0)
            oz = min(az1, bz1) - max(az0, bz0)
            if ox <= 0 and oz <= 0:
                warnings.append(f"体块 {i} 与 {j} 没有搭接，模型会散开")

    for i, w in enumerate(raw.get("windows") or []):
        bw = int(w.get("w", 2))
        bh = int(w.get("h", 2))
        if not (WIN_W[0] <= bw <= WIN_W[1]):
            warnings.append(f"窗 {i}: 宽 {bw} 格，建议 {WIN_W[0]}~{WIN_W[1]}")
        if not (WIN_H[0] <= bh <= WIN_H[1]):
            warnings.append(f"窗 {i}: 高 {bh} 格，建议 {WIN_H[0]}~{WIN_H[1]}")
        if int(w.get("gap_u", 2)) < WIN_GAP_U:
            warnings.append(f"窗 {i}: 横向间距太小，墙垛会断")

    roof = raw.get("roof") or {}
    kind = str(roof.get("type", "flat"))
    if kind in ("gable", "hip"):
        hmin = min(
            min(int(v.get("w", 8)), int(v.get("d", 8))) for v in vols
        )
        if int(roof.get("height", 3)) > max(1, hmin // 2):
            warnings.append(
                f"屋顶高 {roof.get('height')} 格超过 min(宽,深)/2 = {hmin // 2}，会削顶"
            )

    _has_cw = any(
        str(d.get("type")) == "curtain_wall" for d in (raw.get("details") or [])
    )
    if not (raw.get("windows") or []) and not _has_cw:
        warnings.append("没有开窗：整栋楼是实墙，缺少虚实对比")

    details = raw.get("details") or []
    kinds = {str(d.get("type", "")) for d in details}

    # 立面竖向序列有多高？比体块高就是被静默裁掉的层次。
    # 商业立面尤其容易踩：勒脚 2 + 通高玻璃 8 + 檐口带 1 + 招牌带 2 +
    # 上层玻璃 2 = 15 格，而 floors 只给了 2（层高上限 6 ⇒ 12 格）。
    prog = 0
    for d in details:
        if d.get("v") is None:
            continue
        prog = max(prog, int(d.get("v") or 0) + max(1, int(d.get("h") or 1)))
    if prog > total_h:
        warnings.append(
            f"立面构件最高写到第 {prog} 格，但体块只有 {total_h} 格："
            f"上面 {prog - total_h} 格会被静默裁掉（招牌/上层玻璃的 v 写太高）"
        )
    fl_max = max([int(v.get("floors") or 0) for v in vols] or [0])
    # 店面的首层是通高的（通高玻璃 + 挑空），按住宅的 5 格去算会误报"楼层被压掉"。
    per_floor = 8 if _shop else FLOOR_MAX
    if fl_max and prog > fl_max * per_floor:
        warnings.append(
            f"立面写到 {prog} 格，但只声明了 {fl_max} 层（层高上限 {per_floor} 格"
            f" ⇒ {fl_max * per_floor} 格）：floors 要改大，否则最上面那层会被压掉"
        )

    # 幕墙顶到屋顶线，檐口一定盖住它最上面一行（剖面里檐口是从墙顶往外挑的）。
    for d in details:
        if str(d.get("type")) != "curtain_wall":
            continue
        vol = vols[min(int(d.get("volume") or 0), len(vols) - 1)]
        vh = int(vol.get("y", 0)) + int(vol.get("h", 0))
        top = int(vol.get("y", 0)) + int(d.get("v") or 0) + max(1, int(d.get("h") or 1))
        if top >= vh:
            warnings.append(
                f"curtain_wall（u={d.get('u')}）顶边 {top} 已经贴到体块顶 {vh}："
                "檐口会把它最上面一行盖成墙色，玻璃带等于少一行；"
                "把 v 往下挪 1~2 格，或者把体块加高到玻璃之上再压女儿墙"
            )

    if not details:
        warnings.append("没有细节（勒脚/檐口/门/阳台/雨棚）：观感会比较平")
    if "door" not in kinds:
        warnings.append("没有入口门：只有立面，没有「房子」的感觉")
    if "plinth" not in kinds:
        warnings.append("没有勒脚：楼会像浮在空地上")
    if "cornice" not in kinds:
        warnings.append("没有檐口：墙顶是一道生硬的切口")
    if not ({"balcony", "curtain_wall"} & kinds):
        warnings.append("没有阳台或幕墙：立面缺少虚实对比")
    unknown = kinds - {
        "plinth", "cornice", "door", "steps", "balcony", "canopy", "chimney",
        "curtain_wall", "column", "portico", "arcade", "pediment", "dormer",
        "railing", "flagpole", "band", "louver", "planter", "signbox", "lamp",
    }
    if unknown:
        warnings.append(f"不认识的细节类型会被忽略：{'、'.join(sorted(unknown))}")

    warnings.extend(_check_commercial(raw, kinds, roof))
    warnings.extend(_check_ornament(raw))
    return {"ok": not errors, "errors": errors, "warnings": warnings}
