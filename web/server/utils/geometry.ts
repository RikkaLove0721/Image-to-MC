/**
 * 形体兜底：把"照片里看得见、但 AI 忘了写"的形体特征补进蓝图。
 *
 * 为什么需要这一层：
 *
 *   `lean` / `shape` 这类字段，AI 写不写是**概率性**的。照片里上层明显是斜的、
 *   一层明显是六边形，模型有时写对，有时照旧写两个方盒子 —— 于是"更新完的版本
 *   和上一版基本一致"，用户看不出改了什么。
 *
 *   视觉模型已经把这两件事判读出来了（`plan` / `slant_dir`），那就不该再赌
 *   文本模型的心情：判读到"斜"而蓝图里一个 lean 都没有，就在这里按几何规则
 *   补一个。补的是**方向**，不是尺寸 —— 尺寸仍按体块自己的 h 推。
 *
 * 约束：
 *   - 只在"蓝图完全没写"时动手（幂等），AI 已经写了的字段一律不覆盖；
 *   - 只动**上层**那一两个盒子，底层保持水平（整栋都斜会塌）；
 *   - 幅度按体块高度推，单轴不超过 8 格，避免出现"楼要倒了"。
 */

function num(v: any, d = 0): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : d
}

/**
 * 视觉判读 → 核心的 shape 名。
 *
 * "六边形平面"一律落成 **chamfer（四角切角）**，不是真正的 hex：
 * 建筑里的"六边形"几乎都是"矩形把转角斜切掉"，前后长边还在。
 * 用真 hex（两端收成尖）会把正面的长玻璃墙切成两段，玻璃随斜边一格格
 * 往后退，看起来像一排白柱子把玻璃剁碎了 —— 实测过。
 */
const PLAN_MAP: Record<string, string> = {
  hex: 'chamfer',
  hexagon: 'chamfer',
  hex_front: 'chamfer',
  '六边形': 'chamfer',
  oct: 'oct',
  octagon: 'oct',
  '八边形': 'oct',
  round: 'round',
  circle: 'round',
  ellipse: 'round',
  '圆形': 'round',
  wedge: 'wedge',
  '楔形': 'wedge',
  l: 'l',
  lshape: 'l',
  'l形': 'l',
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/**
 * 读体块的竖向坡度。三种写法都认：
 *   {"tilt": {"axis": "x", "bottom": 8, "top": 8}}   整块斜板
 *   {"tilt": {"x": 8}}                               简写 = 底顶同斜率
 *   {"tilt_x": 8} / {"tilt_bottom": 6, "tilt_top": 0} 平铺
 */
function tiltOf(v: any): { axis: string; bottom: number; top: number } {
  const raw = v?.tilt
  if (raw && typeof raw === 'object') {
    const axis = String(raw.axis || '').toLowerCase()
    const bottom = num(raw.bottom)
    const top = num(raw.top)
    if (axis === 'x' || axis === 'z') return { axis, bottom, top }
    const ax = num(raw.x) || num(raw.z)
    if (ax) return { axis: num(raw.x) ? 'x' : 'z', bottom: ax, top: ax }
  }
  for (const axis of ['x', 'z'] as const) {
    const ax = num(v?.[`tilt_${axis}`])
    if (ax) return { axis, bottom: ax, top: ax }
  }
  const bottom = num(v?.tilt_bottom)
  const top = num(v?.tilt_top)
  if (bottom || top) {
    return { axis: /^z/i.test(String(v?.tilt_axis || '')) ? 'z' : 'x', bottom, top }
  }
  return { axis: '', bottom: 0, top: 0 }
}

function setTilt(v: any, axis: string, bottom: number, top: number) {
  if (!v.tilt || typeof v.tilt !== 'object') v.tilt = {}
  v.tilt.axis = axis
  if (bottom) v.tilt.bottom = bottom
  if (top) v.tilt.top = top
}

/** 解析视觉给的坡度字符串："x+8" / "z-5" / "+6"。返回轴与带符号的格数。 */
function parseTiltText(raw: string): { axis: string; mag: number } | null {
  const s = String(raw || '').trim().toLowerCase().replace(/\s+/g, '')
  if (!s || /^(none|无|否|no|n\/a)$/.test(s)) return null
  const m = s.match(/^([xz])?([+-])(\d+)$/)
  if (!m) return null
  const mag = Number(m[3]) * (m[2] === '-' ? -1 : 1)
  if (!mag) return null
  return { axis: m[1] || 'x', mag }
}

function leanOf(v: any): { x: number; z: number } {
  const raw = v?.lean
  if (raw && typeof raw === 'object') return { x: num(raw.x), z: num(raw.z) }
  return { x: num(v?.lean_x), z: num(v?.lean_z) }
}

function skewOf(v: any): { x: number; z: number } {
  const raw = v?.skew
  if (raw && typeof raw === 'object') return { x: num(raw.x), z: num(raw.z) }
  return { x: num(v?.skew_x), z: num(v?.skew_z) }
}

function setLean(v: any, x: number, z: number) {
  if (!v.lean || typeof v.lean !== 'object') v.lean = {}
  if (x) v.lean.x = x
  if (z) v.lean.z = z
}

/** 底座：y 最小的那一批里，面积最大的（主楼基座）。 */
function baseVolume(vols: any[]): any | null {
  const minY = Math.min(...vols.map((v) => num(v.y)))
  const cands = vols.filter((v) => num(v.y) === minY)
  if (!cands.length) return null
  return cands.reduce((a, b) => (num(a.w, 8) * num(a.d, 8) >= num(b.w, 8) * num(b.d, 8) ? a : b))
}

/** 上层盒子：y 最大、且不是屋面小设备间（面积 ≥ 底座 18%、h ≥ 5）。 */
function topVolume(vols: any[], base: any | null): any | null {
  const baseArea = base ? num(base.w, 8) * num(base.d, 8) : 0
  const cands = vols.filter((v) => {
    if (base && v === base) return false
    if (num(v.h, 6) < 5) return false
    if (baseArea > 0 && num(v.w, 8) * num(v.d, 8) < baseArea * 0.18) return false
    return true
  })
  if (!cands.length) return null
  return cands.reduce((a, b) => (num(a.y) >= num(b.y) ? a : b))
}

/** 斜带落点：照片里那条斜着的"带子"通常是**最薄的一层**（中间层），
 *  不是底座、也不是最上面的盒子。找 h 最小的那个上层体块。 */
function bandVolume(vols: any[], base: any | null, top: any | null): any | null {
  const cands = vols.filter((v) => v !== base && num(v.h, 6) >= 3)
  if (!cands.length) return top
  const thin = cands.reduce((a, b) => (num(a.h, 6) <= num(b.h, 6) ? a : b))
  return thin
}

/**
 * 把 vision 判读到的"斜层 / 斜底"补进 spec。返回补了什么。
 *
 * 为什么单独一支：`lean` 只能让**墙**斜，楼板仍然是水平的 ——
 * 照片里"二层像一条左低右高的带子"是**楼板本身斜了**，
 * 用 lean 怎么做都读不出来。`tilt` 才能表达，但 AI 几乎不会主动写，
 * 只能由视觉判读（slant_kind / slant_tilt）来兜底。
 */
export function enforceTilt(spec: any, vision: any): string[] {
  const notes: string[] = []
  const vols: any[] = Array.isArray(spec?.volumes) ? spec.volumes : []
  if (!vols.length || !vision) return notes

  const kind = String(vision?.slant_kind || '').toLowerCase()
  if (kind !== 'band' && kind !== 'soffit') return notes
  const parsed = parseTiltText(String(vision?.slant_tilt || ''))
  // 幂等：蓝图里已经写了坡度就不再动。
  //
  // 这里**故意不做"方向校反"**：一张照片里左右两个盒子的斜向经常相反
  // （向中间收、或向两边张开），视觉判读只能给一个整体方向，
  // 硬按它统一翻转会把本来就对的那一半翻错 —— 实测过，净收益为零。
  if (vols.some((v) => tiltOf(v).bottom || tiltOf(v).top)) return notes
  // 视觉没给坡度、也没说斜：不硬加，正交的楼不该被掰歪
  const text = String(vision?.slant || '')
  const saidNone = !text || /^(none|无|否|no|n\/a)$/i.test(text.trim())
  if (!parsed && saidNone) return notes

  const base = baseVolume(vols)
  const top = topVolume(vols, base)
  const target = kind === 'soffit' ? top || base : bandVolume(vols, base, top)
  if (!target) return notes

  // 方向：视觉给了就用它的；没给就从那句话里认"哪头高"
  let mag = parsed?.mag || 0
  if (!mag) {
    const hiRight = /(左低右高|右高|向右升|往右升|右侧高|东高)/.test(text)
    const hiLeft = /(右低左高|左高|向左升|往左升|左侧高|西高)/.test(text)
    const sign = hiLeft && !hiRight ? -1 : 1
    mag =
      sign *
      (kind === 'band'
        ? clamp(Math.round(num(target.w, 10) * 0.2), 4, 10)
        : clamp(Math.round(num(target.h, 6) * 0.6), 3, 7))
  }
  const axis = parsed?.axis || 'x'

  if (kind === 'band') {
    setTilt(target, axis, mag, mag)
    notes.push(
      `照片判读中层是**斜带**（楼板斜着走），蓝图里只有水平楼层 —— 已给第 ${vols.indexOf(target) + 1} ` +
        `个体块补 "tilt": {"axis": "${axis}", "bottom": ${mag}, "top": ${mag}}`,
    )
  } else {
    setTilt(target, axis, mag, 0)
    notes.push(
      `照片判读有**斜底悬挑**（底面被斜切），已给第 ${vols.indexOf(target) + 1} ` +
        `个体块补 "tilt": {"axis": "${axis}", "bottom": ${mag}}`,
    )
  }
  return notes
}

/**
 * 把 vision 判读到的"平面形状 / 倾斜方向"补进 spec。返回补了什么（给日志和界面看）。
 */
export function enforceShape(spec: any, vision: any): string[] {
  const notes: string[] = []
  const vols: any[] = Array.isArray(spec?.volumes) ? spec.volumes : []
  if (!vols.length || !vision) return notes

  const hasPlan = vols.some(
    (v) =>
      !!v.shape ||
      (Array.isArray(v.poly) && v.poly.length >= 3) ||
      num(v.chamfer) > 0,
  )
  const hasLean = vols.some((v) => {
    const L = leanOf(v)
    const S = skewOf(v)
    return !!(L.x || L.z || S.x || S.z)
  })

  // ---- 1. 平面轮廓：六边形楼层 / 八边形塔楼 ----
  const wantPlan = PLAN_MAP[String(vision?.plan || '').toLowerCase()]
  if (wantPlan && !hasPlan) {
    // 只给能"切出角"的体块：8×8 以下切一刀就只剩一条缝
    const cands = vols
      .filter((v) => num(v.w, 8) >= 9 && num(v.d, 8) >= 9)
      .sort((a, b) => num(a.y) - num(b.y) || num(b.w, 8) * num(b.d, 8) - num(a.w, 8) * num(a.d, 8))
    const target = cands[0]
    if (target) {
      if (wantPlan === 'chamfer') {
        // 切角取短边的 1/4：太小看不出是六边形，太大正面就没墙了
        const w = num(target.w, 20)
        const d = num(target.d, 12)
        target.chamfer = clamp(Math.round(Math.min(w, d) * 0.22), 4, 10)
        notes.push(
          `照片判读一层平面是六边形${vision?.plan_note ? `（${vision.plan_note}）` : ''}，` +
            `已给底层补 "chamfer": ${target.chamfer}（四角斜切；真正的六角形才用 shape）`,
        )
      } else {
        target.shape = wantPlan
        notes.push(
          `照片判读为一层平面 ${vision?.plan}${
            vision?.plan_note ? `（${vision.plan_note}）` : ''
          }，已给底层补 "shape": "${wantPlan}"`,
        )
      }
    }
  }

  // ---- 2. 斜置：判读到"斜"但蓝图里一个都没写 → 按方向补 ----
  // 判读成"整层斜带"时，斜由 tilt 表达（enforceTilt 已经补过）。
  // 这里再叠一个 lean 会让墙往外倒，反而离开照片 —— 斜带本身不需要墙也歪。
  if (String(vision?.slant_kind || '').toLowerCase() === 'band') return notes
  let dir = String(vision?.slant_dir || '').toLowerCase()
  const text = String(vision?.slant || '')
  const saidNone = !text || /^(none|无|否|no|n\/a)$/i.test(text.trim())
  if (!['out', 'front', 'both', 'skew'].includes(dir)) {
    // 老判读只给一句话：从话里认方向，认不出但明确"斜"就按 both
    // （照片里最常出现的是"又朝外又朝前"，只补一个方向等于没补）
    if (!saidNone) {
      const out = /(朝外|向外|往外|偏出|out)/.test(text)
      const fwd = /(朝前|向前|往前|朝街|镜头|front)/.test(text)
      dir = out && fwd ? 'both' : fwd ? 'front' : 'out'
    }
  }
  if (!['out', 'front', 'both', 'skew'].includes(dir)) return notes

  const base = baseVolume(vols)
  const top = topVolume(vols, base)
  if (!top) return notes
  const h = Math.max(3, num(top.h, 6))
  const mag = clamp(Math.round(h * 0.35), 3, 7)
  const magZ = clamp(Math.round(h * 0.28), 3, 5)

  // 朝外：往"它已经探出的那一侧"倒，没有明显探出就取面积重心偏的一侧
  let sx = 1
  if (base) {
    const bx1 = num(base.x) + num(base.w, 8)
    const tx1 = num(top.x) + num(top.w, 8)
    if (num(top.x) < num(base.x)) sx = -1
    else if (tx1 > bx1) sx = 1
    else sx = num(top.x) + num(top.w, 8) / 2 >= num(base.x) + num(base.w, 8) / 2 ? 1 : -1
  }

  const L = leanOf(top)
  if (dir === 'skew') {
    if (!skewOf(top).z) {
      top.skew = { ...(top.skew || {}), z: magZ * sx }
      notes.push(`照片判读为平面斜摆，已给上层补 "skew": {"z": ${magZ * sx}}`)
    }
    return notes
  }

  // 已经写了一个方向、但照片是两个方向：只补缺的那一轴
  if ((L.x || L.z) && dir === 'both') {
    if (!L.x || !L.z) {
      setLean(top, L.x || mag * sx, L.z || magZ)
      notes.push(
        `照片判读为"又朝外又朝前"斜，已把上层 lean 补成 {"x": ${L.x || mag * sx}, "z": ${L.z || magZ}}`,
      )
    }
    return notes
  }
  if (L.x || L.z || hasLean) return notes

  if (dir === 'out') {
    setLean(top, mag * sx, 0)
    notes.push(`照片判读为上层朝外斜，已补 "lean": {"x": ${mag * sx}}`)
  } else if (dir === 'front') {
    setLean(top, 0, magZ)
    notes.push(`照片判读为上层朝前（朝街）斜，已补 "lean": {"z": ${magZ}}`)
  } else {
    setLean(top, mag * sx, magZ)
    notes.push(
      `照片判读为上层"又朝外又朝前"斜，已补 "lean": {"x": ${mag * sx}, "z": ${magZ}}`,
    )
  }
  return notes
}

/**
 * 修改接口用：把上一版的形体字段钉回去。
 *
 * "修改"是让模型重写整份 JSON，它经常把不认识的字段（shape / lean）顺手丢掉 ——
 * 用户只让它改个颜色，结果斜的那一层变回了方块。按体块序号补齐。
 */
export function pinShape(next: any, prev: any): string[] {
  const notes: string[] = []
  const a: any[] = Array.isArray(next?.volumes) ? next.volumes : []
  const b: any[] = Array.isArray(prev?.volumes) ? prev.volumes : []
  const KEYS = [
    'shape', 'poly', 'chamfer',
    'lean', 'lean_x', 'lean_z',
    'skew', 'skew_x', 'skew_z',
    'tilt', 'tilt_axis', 'tilt_x', 'tilt_z', 'tilt_bottom', 'tilt_top',
  ]
  for (let i = 0; i < a.length && i < b.length; i++) {
    for (const k of KEYS) {
      const was = b[i]?.[k]
      if (was === undefined) continue
      const now = a[i]?.[k]
      const empty =
        now === undefined ||
        (typeof now === 'object' && now && !Object.values(now).some((x) => num(x) !== 0)) ||
        (typeof now === 'string' && !now) ||
        (now === 0 && typeof was === 'object')
      if (empty) {
        a[i][k] = JSON.parse(JSON.stringify(was))
        notes.push(`第 ${i + 1} 个体块的 ${k} 被 AI 写丢，已按上一版补回`)
      }
    }
  }
  return notes
}
