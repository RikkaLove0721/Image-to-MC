<script setup lang="ts">
/**
 * 小C：坐在蓝色方块上的看板娘。
 *
 * 为什么不用真 Live2D：
 *   Live2D 需要 .moc3（Cubism 建模软件导出）+ Cubism Core 运行时，
 *   没有建模软件就做不出这套资源。这里用社区常见的降级方案 ——
 *   "分层立绘 + 程序动画"：每一层各自做自己的动作，叠起来接近待机动作。
 *
 * 分层（从下到上）：
 *   方块（完全静止） → 身体（呼吸） → 腿（绕膝盖摆） → 眼白+眼线 → 虹膜 ×2 → 闭眼版
 *
 * 三条硬约束（都是踩过的坑）：
 *   1. 腿层的上沿必须落在身体还留着腿的区间里（素材端重叠 4%），
 *      两边对切时旋转产生的抗锯齿缝会露出方块，看起来是"大腿上一道横线"。
 *   2. 投影只能加在 .xiaoc-figure 这一层（对整组渲染结果做 drop-shadow）。
 *      加在身体层上时，身体被切掉的那条水平边会投出一条阴影横线，同样是"断腿"。
 *   3. 视线只动虹膜，眼框不动。整只眼睛平移看起来是"整张脸在滑"。
 *      虹膜装在被裁成椭圆的小盒子里，所以永远飞不出眼睛。
 */
/**
 * 台词不写在这里：`app/utils/xiaoc-lines.ts` 里按场景分组（搭话 / 点击 /
 * 开始砌 / 砌完 / 看图 / 转模型 …），别的组件用 `xiaocTell(kind, ctx)` 触发。
 * 这个组件只负责"要不要说、现在能不能说"。
 */
type SayKind =
  | 'hello' | 'idle' | 'idle-night' | 'click' | 'drag'
  | 'build-start' | 'build-done' | 'build-fail'
  | 'plan-start' | 'plan-done' | 'plan-fail'
  | 'viewer-open' | 'view-change' | 'model-select'
  | 'hide' | 'restore'
interface SayCtx { model?: string; elapsed?: number; score?: number; view?: string }

/** 各场景的冷却（毫秒）：转视角、换模型这类会连续触发，不能每下都说 */
const COOLDOWN: Partial<Record<SayKind, number>> = {
  'view-change': 9000,
  'model-select': 8000,
  drag: 12000,
  click: 2500,
  'plan-start': 20000,
  'build-start': 20000,
  'build-done': 1500,
}
/** 剧情性最强的几句，可以打断正在显示的普通台词 */
const IMPORTANT: SayKind[] = ['build-done', 'build-fail', 'plan-done', 'plan-fail']

/** 素材是 273×527，显示尺寸见 CSS；眼睛与虹膜的位置按素材像素写，模板里换算成百分比 */
const ASSET = { w: 273, h: 527 }
const EYES = [
  { x: 124, y: 74, w: 24, h: 22 },
  { x: 165, y: 73, w: 23, h: 22 },
]

const hidden = ref(false)
const panel = ref(false)
/** 显示比例 70%~160%，用户拖滑条改，记在 localStorage */
const zoomPct = ref(100)
/** 眨眼：闭眼层叠上来再撤掉，间隔模拟 Live2D 的 3.5s ± 1.2s */
const blinking = ref(false)
const bubble = ref('')
const dragging = ref(false)
/** 相对右下角的偏移：默认贴右下角，拖过之后记住新位置 */
const pos = reactive({ x: 0, y: 0 })
/** 视差：指针位置 -1~1，让身体有一点点跟随感 */
const para = reactive({ x: 0, y: 0 })
/** 虹膜跟随指针的方向（-1~1），与视差分开放，幅度不同 */
const eyeDir = reactive({ x: 0, y: 0 })

let bubbleTimer: ReturnType<typeof setTimeout> | null = null
let blinkTimer: ReturnType<typeof setTimeout> | null = null
let talkTimer: ReturnType<typeof setTimeout> | null = null
let dragStart = { x: 0, y: 0, px: 0, py: 0, moved: false }

const pct = (v: number, base: number) => `${(v / base) * 100}%`

/** 虹膜小盒子：正好套住一只眼睛，椭圆裁剪 ⇒ 虹膜出不去 */
function irisBox(e: { x: number; y: number; w: number; h: number }) {
  return {
    left: pct(e.x, ASSET.w),
    top: pct(e.y, ASSET.h),
    width: pct(e.w, ASSET.w),
    height: pct(e.h, ASSET.h),
  }
}

/** 盒子里那整张虹膜图：反向偏移，叠起来与身体严丝合缝 */
function irisImg(e: { x: number; y: number; w: number; h: number }) {
  return {
    width: pct(ASSET.w, e.w),
    left: pct(-e.x, e.w),
    top: pct(-e.y, e.h),
  }
}

const lastSaidAt: Partial<Record<SayKind, number>> = {}
let lastAnyAt = 0

/** 说话。给了 text 就直接说；否则按 kind 抽一句。返回是否真的说了。 */
function say(text?: string, kind?: SayKind, ctx: SayCtx = {}): boolean {
  const now = Date.now()
  const k = kind || 'idle'
  if (kind) {
    if (now - (lastSaidAt[k] || 0) < (COOLDOWN[k] ?? 4000)) return false
    // 普通场景不打断正在进行的重要台词
    if (now - lastAnyAt < 1400 && !IMPORTANT.includes(k)) return false
  }
  lastSaidAt[k] = now
  lastAnyAt = now
  bubble.value = text || xiaocLine(k, ctx)
  if (bubbleTimer) clearTimeout(bubbleTimer)
  bubbleTimer = setTimeout(() => (bubble.value = ''), 4200)
  return true
}

/** 别的组件通过 xiaocTell() 发来的事件 */
function onTell(e: Event) {
  if (hidden.value) return
  const d = (e as CustomEvent).detail || {}
  if (!d.kind) return
  say(undefined, d.kind as SayKind, d.ctx || {})
}

/** 随机搭话：平均 30 秒一次，浮动 ±6 秒，避免像定时器一样准点 */
function scheduleTalk() {
  talkTimer = setTimeout(
    () => {
      const h = new Date().getHours()
      const kind: SayKind = h >= 23 || h < 6 ? 'idle-night' : 'idle'
      if (!hidden.value && !panel.value && !dragging.value && !bubble.value) say(undefined, kind)
      scheduleTalk()
    },
    24_000 + Math.random() * 12_000,
  )
}

function startDrag(e: PointerEvent) {
  if (e.button !== 0) return
  dragging.value = true
  dragStart = { x: e.clientX, y: e.clientY, px: pos.x, py: pos.y, moved: false }
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}

function onDrag(e: PointerEvent) {
  if (!dragging.value) return
  const dx = e.clientX - dragStart.x
  const dy = e.clientY - dragStart.y
  if (Math.abs(dx) + Math.abs(dy) > 3) dragStart.moved = true
  // 元素靠 right/bottom 定位，translate 正值才是"往右/往下"。
  pos.x = dragStart.px + dx
  pos.y = dragStart.py + dy
}

function endDrag(e: PointerEvent) {
  if (!dragging.value) return
  dragging.value = false
  ;(e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId)
  if (!dragStart.moved) {
    // 没拖动 = 左键点击：开/关设置弹窗
    panel.value = !panel.value
    if (panel.value) say(undefined, 'click')
    return
  }
  say(undefined, 'drag')
  try {
    localStorage.setItem('xiaoc-pos', JSON.stringify({ x: pos.x, y: pos.y }))
  } catch {
    /* 隐私模式存不了就算了 */
  }
}

function onMove(e: PointerEvent) {
  if (dragging.value || hidden.value) return
  para.x = (e.clientX / window.innerWidth - 0.5) * 2
  para.y = (e.clientY / window.innerHeight - 0.5) * 2
  // 虹膜幅度比整体视差大：整图挪 1.6px 看不出"在看"，虹膜挪 1.6px 就能读出来
  eyeDir.x = para.x
  eyeDir.y = para.y
}

function hide() {
  say(undefined, 'hide')
  hidden.value = true
  panel.value = false
}

function restore() {
  hidden.value = false
  say(undefined, 'restore')
}

watch(zoomPct, (v) => {
  try {
    localStorage.setItem('xiaoc-zoom', String(v))
  } catch {
    /* 忽略 */
  }
})

onMounted(() => {
  try {
    const raw = localStorage.getItem('xiaoc-pos')
    if (raw) {
      const p = JSON.parse(raw)
      if (typeof p.x === 'number' && typeof p.y === 'number') {
        pos.x = p.x
        pos.y = p.y
      }
    }
    const z = Number(localStorage.getItem('xiaoc-zoom'))
    if (z >= 70 && z <= 160) zoomPct.value = z
  } catch {
    /* 忽略 */
  }
  scheduleBlink()
  scheduleTalk()
  window.addEventListener('pointermove', onMove)
  window.addEventListener('xiaoc:tell', onTell)
  setTimeout(() => say(undefined, 'hello'), 1200)
})

function scheduleBlink() {
  blinkTimer = setTimeout(() => {
    blinking.value = true
    setTimeout(() => (blinking.value = false), 150)
    scheduleBlink()
  }, 2400 + Math.random() * 2600)
}

onBeforeUnmount(() => {
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('xiaoc:tell', onTell)
  if (blinkTimer) clearTimeout(blinkTimer)
  if (bubbleTimer) clearTimeout(bubbleTimer)
  if (talkTimer) clearTimeout(talkTimer)
})

// 点外面收起弹窗
function onDocDown(e: PointerEvent) {
  if (!panel.value) return
  const el = e.target as HTMLElement
  if (!el.closest('.xiaoc')) panel.value = false
}
onMounted(() => document.addEventListener('pointerdown', onDocDown))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocDown))
</script>

<template>
  <div
    class="xiaoc"
    :class="{ dragging, hidden }"
    :style="{
      transform: `translate(${pos.x}px, ${pos.y}px)`,
      '--px': para.x,
      '--py': para.y,
      '--ex': eyeDir.x,
      '--ey': eyeDir.y,
      // --zu 是用户设定；--z 是实际生效的（窄屏会再乘 0.88），
      // 气泡/弹窗要按 --z 抬，否则放大后会被人物挡住
      '--zu': zoomPct / 100,
    }"
  >
    <div v-if="bubble" class="xiaoc-bubble">{{ bubble }}</div>

    <!-- 设置弹窗：不跟着角色缩放，否则文字会一起变大变小 -->
    <div v-if="panel" class="xiaoc-panel" @pointerdown.stop>
      <div class="panel-head">
        <span class="panel-name">小C</span>
        <button class="panel-x" title="收起" @click="panel = false">×</button>
      </div>
      <label class="panel-row">
        <span>大小</span>
        <input v-model.number="zoomPct" type="range" min="70" max="160" step="5" />
        <b>{{ zoomPct }}%</b>
      </label>
      <button class="panel-close" @click="hide">关闭小C</button>
    </div>

    <button v-if="hidden" class="xiaoc-restore" title="叫小C出来" @click="restore">
      <UiIcon name="spark" :size="16" />
    </button>

    <div
      v-else
      class="xiaoc-stage"
      @pointerdown="startDrag"
      @pointermove="onDrag"
      @pointerup="endDrag"
    >
      <div class="xiaoc-body">
        <!-- 方块是"她坐的东西"，必须完全静止 -->
        <img class="xiaoc-block" src="/xiaoc/block.png" alt="" draggable="false" />
        <div class="xiaoc-figure" :class="{ blinking }">
          <img class="xiaoc-bodyimg" src="/xiaoc/xiaoc-body.png" alt="小C" draggable="false" />
          <img class="xiaoc-legs" src="/xiaoc/xiaoc-legs.png" alt="" draggable="false" />
          <img class="xiaoc-eye" src="/xiaoc/xiaoc-eye.png" alt="" draggable="false" />
          <!-- 虹膜：每只装在一个椭圆裁剪的小盒子里，所以只会转、不会飞出去 -->
          <span v-for="(e, i) in EYES" :key="i" class="xiaoc-irisbox" :style="irisBox(e)">
            <img class="xiaoc-iris" :src="`/xiaoc/xiaoc-iris-${i}.png`" :style="irisImg(e)" alt="" draggable="false" />
          </span>
          <img
            class="xiaoc-eye xiaoc-eye-blink"
            src="/xiaoc/xiaoc-eye-blink.png"
            alt=""
            draggable="false"
          />
        </div>
        <div class="xiaoc-shadow" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.xiaoc {
  position: fixed;
  right: 18px;
  bottom: -4px;
  z-index: 60;
  /* 实际缩放：用户设定 ×（窄屏 0.88）。transform 不影响布局，
     所以气泡要靠这个值自己往上抬。 */
  --z: var(--zu, 1);
  transition: transform 0.18s ease-out;
  user-select: none;
  -webkit-user-select: none;
}
.xiaoc.dragging {
  transition: none;
}

/* 缩放只作用在这一层：方块、人物、影子一起变，弹窗不变 */
.xiaoc-stage {
  cursor: grab;
  touch-action: none;
  transform: scale(var(--z, 1));
  transform-origin: right bottom;
  /* 明确压在气泡/弹窗下面：DOM 顺序上人物在后，不设层级就会盖住文字 */
  z-index: 1;
}
.xiaoc.dragging .xiaoc-stage {
  cursor: grabbing;
}

.xiaoc-body {
  position: relative;
  width: 190px;
  height: 273px;
}

/* 方块：完全静止。它一旦跟着晃，"坐"的感觉就没了，只剩整张卡片在飘 */
.xiaoc-block {
  position: absolute;
  left: 0;
  bottom: 0;
  width: 190px;
  height: auto;
  pointer-events: none;
  filter: drop-shadow(0 10px 14px rgba(24, 42, 80, 0.16));
}

/* 人物外层：呼吸 + 摇摆 + 视差都作用在这一层，原点压在臀部（62%），
   所以屁股贴着方块顶面不动，动的只有上身和头发。
   投影也只加在这一层 —— 加在子层上时，层被切掉的那条水平边会投出一条横线。 */
.xiaoc-figure {
  position: absolute;
  left: 42px;
  top: -9px;
  width: 134px;
  height: 259px;
  pointer-events: none;
  transform-origin: 50% 62%;
  animation: xiaoc-breathe 5.2s ease-in-out infinite;
  translate: calc(var(--px) * -1.6px) calc(var(--py) * -1px);
  transition: translate 0.35s ease-out;
  filter: drop-shadow(0 3px 4px rgba(24, 42, 80, 0.12));
}

.xiaoc-bodyimg {
  display: block;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

/* 腿：绕膝盖旋转。图层上沿与身体重叠 4%（素材端保证），
   所以旋转时的抗锯齿缝下面永远有皮肤，不会露出方块。 */
.xiaoc-legs {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  transform-origin: 52.5% 68%;
  animation: xiaoc-legswing 2.9s ease-in-out infinite;
}

/* 眼白 + 眼线 + 睫毛：这一层不动 */
.xiaoc-eye {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

/* 虹膜盒子：椭圆裁剪。幅度 1.3px / 1px —— 眼睛本身只有 11px 宽，
   再大就会贴到眼线，看着像斜视；溢出部分由椭圆裁剪兜住 */
.xiaoc-irisbox {
  position: absolute;
  overflow: hidden;
  border-radius: 50%;
  pointer-events: none;
}

.xiaoc-iris {
  position: absolute;
  height: auto;
  max-width: none;
  transform: translate(calc(var(--ex, 0) * 1.3px), calc(var(--ey, 0) * 1px));
  transition: transform 0.22s ease-out;
}

.xiaoc-eye-blink {
  opacity: 0;
}

.xiaoc-figure.blinking .xiaoc-eye-blink {
  opacity: 1;
}

.xiaoc-figure.blinking .xiaoc-eye:not(.xiaoc-eye-blink),
.xiaoc-figure.blinking .xiaoc-irisbox {
  opacity: 0;
}

.xiaoc.dragging .xiaoc-figure,
.xiaoc.dragging .xiaoc-legs,
.xiaoc.dragging .xiaoc-shadow {
  animation-play-state: paused;
}

@keyframes xiaoc-legswing {
  0%,
  100% {
    transform: rotate(1.4deg);
  }
  50% {
    transform: rotate(-1.6deg);
  }
}

.xiaoc-shadow {
  position: absolute;
  left: 22%;
  right: 18%;
  bottom: 2px;
  height: 16px;
  border-radius: 50%;
  background: radial-gradient(ellipse at center, rgba(30, 50, 90, 0.24), transparent 70%);
  filter: blur(3px);
  animation: xiaoc-shadow 5.2s ease-in-out infinite;
}

@keyframes xiaoc-breathe {
  0%,
  100% {
    transform: rotate(-1.1deg) scale(1);
  }
  50% {
    transform: rotate(1.3deg) scale(1.018);
  }
}

@keyframes xiaoc-shadow {
  0%,
  100% {
    transform: scaleX(1);
    opacity: 0.5;
  }
  50% {
    transform: scaleX(0.95);
    opacity: 0.4;
  }
}

.xiaoc-bubble {
  position: absolute;
  right: 12px;
  /* 人物的布局盒永远是 273px（transform 撑不开布局），
     所以放大时必须按 --z 把气泡抬到头顶以上，否则字被人挡住。 */
  bottom: calc(273px * var(--z, 1) + 6px);
  z-index: 3;
  max-width: 200px;
  padding: 8px 12px;
  background: #fff;
  border: 1px solid var(--line, #e6e3dc);
  border-radius: 12px 12px 2px 12px;
  box-shadow: 0 8px 22px rgba(30, 40, 60, 0.12);
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text, #23211e);
  white-space: normal;
  animation: xiaoc-pop 0.18s ease-out;
}

/* 弹窗挂在 .xiaoc 上而不是 .xiaoc-stage 上，所以不跟着缩放 */
.xiaoc-panel {
  position: absolute;
  right: 0;
  /* 同样按缩放抬；min() 防止放大到 160% 时跑到窗口外面点不到 */
  bottom: min(calc(273px * var(--z, 1) + 12px), 72vh);
  z-index: 4;
  width: 216px;
  padding: 12px 13px 13px;
  background: #fff;
  border: 1px solid var(--line, #e6e3dc);
  border-radius: 14px;
  box-shadow: 0 14px 34px rgba(30, 40, 60, 0.16);
  animation: xiaoc-pop 0.16s ease-out;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.panel-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text, #23211e);
}

.panel-x {
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--faint, #9a958c);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
}

.panel-x:hover {
  background: rgba(0, 0, 0, 0.05);
}

.panel-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted, #6d675e);
}

.panel-row input[type='range'] {
  flex: 1;
  accent-color: var(--brand-deep, #2f6fde);
}

.panel-row b {
  width: 38px;
  text-align: right;
  font-weight: 600;
  color: var(--text, #23211e);
  font-variant-numeric: tabular-nums;
}

.panel-close {
  width: 100%;
  margin-top: 11px;
  padding: 7px 0;
  border: 1px solid var(--line, #e6e3dc);
  border-radius: 9px;
  background: #fff;
  color: #c2413b;
  font-size: 12.5px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.panel-close:hover {
  background: #fdf1f0;
  border-color: #f0cfcc;
}

@keyframes xiaoc-pop {
  from {
    opacity: 0;
    transform: translateY(4px) scale(0.96);
  }
}

.xiaoc-restore {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  margin-bottom: 16px;
  border-radius: 50%;
  border: 1px solid var(--line, #e6e3dc);
  background: #fff;
  color: var(--brand-deep, #2f6fde);
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(30, 40, 60, 0.12);
}

/* 窄屏不再单独改尺寸：.xiaoc-body 的内部全是绝对定位的百分比，
   改宽高会让图与图错位。改用默认比例 88%（弹窗里的滑条仍可调） */
@media (max-width: 720px) {
  .xiaoc {
    --z: calc(var(--zu, 1) * 0.88);
  }
}
</style>
