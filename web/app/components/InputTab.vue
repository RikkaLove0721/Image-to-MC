<script setup lang="ts">
type Role = 'front' | 'side' | 'top' | 'free'

const props = defineProps<{
  projectId: string
  images: Record<string, { url: string; original?: string; bytes?: number; uploadedAt?: string } | undefined>
  modelCount: number
  running: any
  /** 这个项目上次用的生成设置（没有 = 新项目，用默认值） */
  settings?: {
    scale?: number
    detail?: number
    craft?: number
    complexity?: number
    hint?: string
    width?: number
  } | null
}>()

const emit = defineEmits<{ refresh: []; generated: [name: string]; toast: [msg: string, type?: 'ok' | 'err'] }>()

const ROLE_META: { role: Role; label: string; hint: string; required?: boolean }[] = [
  { role: 'front', label: '正视图', hint: '正对正面，能看清窗阵', required: true },
  { role: 'side', label: '侧视图', hint: '从侧面拍，用来量进深' },
  { role: 'top', label: '俯视图', hint: '俯拍或平面图，定轮廓' },
  { role: 'free', label: '自由视角', hint: '任意角度，校准整体颜色' },
]

/**
 * 尺寸 = 比例尺（1 米 = 多少格），不再是"写死多少格宽"。
 *
 * AI 先判断建筑类型与实际尺寸（米），再用这个比例尺换算成格数。
 * 这样"高度大于宽度"的窄楼不会被强行铺成一条长板。
 * 范围 0.5~8 格/米：0.5 是袖珍摆件尺度，8 是一格约 12.5 厘米的超精细尺度。
 */
const SCALE_MIN = 0.5
const SCALE_MAX = 8
const SCALE_DEFAULT = 2
const scale = ref(SCALE_DEFAULT)

/**
 * 游戏建筑技巧性 0/1/2（低/中/高），默认中。
 * 这不是"精细度"，是"风格取向"：高 → 优先套用 MC 社区建筑法则，
 * 允许偏离照片的逐像素还原；低 → 尽量照抄照片。
 */
const craft = ref(1)
const CRAFT_LABEL = ['低 · 贴近照片', '中 · 还原为主', '高 · 社区法则优先']
const detailLevel = ref(2)
const DETAIL_LABEL: Record<number, string> = { 1: '基础', 2: '标准', 3: '精细' }

/**
 * 结构数量 1~5：建筑由几个部分组成。
 *
 * 和"细节等级"不是一回事：细节等级管的是线脚、窗套这类贴面装饰；
 * 结构数量管的是**体量**——角塔、侧翼、门厅、凸窗、楼梯间、屋面设备。
 * 照片里的建筑通常有几十个组成部分，而 AI 一次只写得动三五个体块，
 * 所以这一档不由 AI 写，交给构建器按规则长（见 mc12/spec.py 的 _grow_volumes）。
 */
const complexity = ref(2)
const COMPLEXITY_LABEL: Record<number, string> = {
  1: '简易 · 只有主体',
  2: '基本 · 两角塔',
  3: '标准 · 四角塔 + 凸窗',
  4: '精细 · 加侧翼门厅',
  5: '复杂 · 二三十个结构',
}

/** AI 上次判断出来的建筑实际尺寸（米），自动选尺寸时要用 */
const buildingMeters = ref<number[] | null>(null)
const buildingType = ref('')

/** 按"模型别太大也别太小"反推一个比例尺：目标约 3500 块的表面积 */
function autoScale() {
  const m = buildingMeters.value || [15, 18, 12]
  const [bw, bh, bd] = m
  const faces = 2 * (bw * bh + bh * bd + bw * bd) // 平方米
  const target = 3600 // 方块数，中等体量的观感上限
  const raw = Math.sqrt(target / Math.max(faces, 1))
  const snapped = Math.round(raw * 2) / 2 // 对齐到 0.5
  scale.value = Math.min(SCALE_MAX, Math.max(SCALE_MIN, snapped))
  emit('toast', `按 ${bw}×${bh}×${bd} 米估算：${scale.value.toFixed(1)} 格/米`)
}

const scaleHint = computed(() => {
  const m = buildingMeters.value
  if (!m) return ''
  const w = Math.round(m[0] * scale.value)
  const h = Math.round(m[1] * scale.value)
  const d = Math.round(m[2] * scale.value)
  return `≈ ${w} × ${h} × ${d} 格`
})

const aiCfg = reactive({
  baseUrl: '',
  model: '',
  hasKey: false,
  keyHint: '',
  keyInput: '',
  hint: '',
  // 视觉：留空就是"用主模型自己看图"，填了才换成单独的视觉模型
  visionBaseUrl: '',
  visionModel: '',
  hasVisionKey: false,
  visionKeyHint: '',
  visionKeyInput: '',
  visionOpen: false,
  // 探测结论：true 能读图 / false 读不出 / null 还没测
  visionEffective: '',
  visionOk: null as boolean | null,
  visionNote: '',
  probing: false,
})
const aiSpec = ref('')
const aiBusy = ref(false)
/** 方案生成已用时（秒）。界面不显示这个的话，两分钟的不动光标看起来就是卡死 */
const planElapsed = ref(0)
let planTimer: ReturnType<typeof setInterval> | null = null
/** 服务端真实提供的模型名，用于输入框下拉提示（模型名写错最难自查） */
const modelList = ref<string[]>([])
const aiBrief = ref<any>(null)
const aiVision = ref<any>(null)
const aiVisionDiff = ref<any>(null)
/** 主体是舰船时，判读的那套字段（层数/开间/屋顶）没意义，换成船长和甲板设备 */
const isShipVision = computed(() =>
  ['ship', 'vessel', 'warship', 'boat'].includes(String(aiVision.value?.subject || '')),
)
const bowLabel = computed(() => {
  const m: Record<string, string> = {
    left: '画面左侧',
    right: '画面右侧',
    toward: '正对镜头',
    away: '背对镜头',
  }
  return m[String(aiVision.value?.ship?.bow || '')] || ''
})
/** 服务端给的额外说明（比如"主体不是建筑，构建器没有这套语汇"） */
const aiNote = ref('')
const aiError = ref('')
/** 这一次生成的身份：种子用于复现，得分/轮次用于判断"够不够像" */
const aiSeed = ref<number | null>(null)
/** 这一轮方案花了多少 token（仓库页展示） */
const aiUsage = ref<any>(null)
/** 分析过程快照：仓库页「修改」时要把它喂回 AI */
const aiAnalysis = ref<any>(null)
const aiScore = ref<number | null>(null)
/** 照片被裁掉一侧时，按对称补全的记录：{cut:['left'], ratio:1.28} */
const aiComplete = ref<{ cut: string[]; ratio: number } | null>(null)
const aiRounds = ref<{ round: number; score: number; issues: string[] }[]>([])

/** 标题栏那一行小字：说清"谁来看图、测得怎么样"。 */
const visionStateText = computed(() => {
  if (!aiCfg.hasKey) return '未配置'
  const who = aiCfg.visionEffective || aiCfg.model || '—'
  if (aiCfg.visionOk === true) return `能读图 · ${who}`
  if (aiCfg.visionOk === false) return `读不出图 · ${who}`
  return `待检测 · ${who}`
})

const job = ref<any>(null)
const log = ref('')
const status = ref<'idle' | 'running' | 'done' | 'failed'>('idle')
const elapsed = ref(0)
const uploading = ref<Role | null>(null)
const dragRole = ref<Role | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const pendingRole = ref<Role>('front')
let pollTimer: ReturnType<typeof setTimeout> | null = null
let pollFails = 0
let tickTimer: ReturnType<typeof setInterval> | null = null

const imageCount = computed(() => ROLE_META.filter((r) => props.images?.[r.role]).length)

const progress = computed(() => {
  if (status.value === 'done') return 100
  const matches = [...log.value.matchAll(/\[(\d+)\/(\d+)\]/g)]
  if (!matches.length) return status.value === 'running' ? 4 : 0
  const last = matches[matches.length - 1]
  return Math.min(97, Math.round((Number(last[1]) / Number(last[2])) * 100))
})

const hasLog = computed(() => log.value.trim().length > 0)

const logLines = computed(() =>
  log.value
    .replace(/\r/g, '')
    .split('\n')
    .map((text) => {
      let cls = ''
      if (/^\[\d+\/\d+\]/.test(text)) cls = 'step'
      else if (/校验: 通过|生成完成|总方块数|输出目录/.test(text)) cls = 'ok'
      else if (/失败|错误|Traceback|Error|无法启动/.test(text)) cls = 'err'
      else if (/跳过|忽略|注:|警告|退回|估算/.test(text)) cls = 'warn'
      return { text, cls }
    }),
)

/* ---- 视角素材 ---- */

function pick(role: Role) {
  if (uploading.value) return
  pendingRole.value = role
  fileInput.value?.click()
}

async function onChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  await upload(pendingRole.value, file)
}

function onDrop(role: Role, event: DragEvent) {
  dragRole.value = null
  const file = event.dataTransfer?.files?.[0]
  if (file) upload(role, file)
}

async function upload(role: Role, file: File, quiet = false) {
  if (!file.type.startsWith('image/')) {
    emit('toast', '只能上传图片文件', 'err')
    return
  }
  const fd = new FormData()
  fd.append('role', role)
  fd.append('file', file)
  uploading.value = role
  try {
    await $fetch(`/api/projects/${props.projectId}/images`, { method: 'POST', body: fd })
    emit('refresh')
    if (!quiet) emit('toast', `${ROLE_META.find((r) => r.role === role)?.label} 已上传`)
  } catch (err: any) {
    emit('toast', err?.data?.statusMessage || '上传失败', 'err')
  } finally {
    uploading.value = null
  }
}

/* ---- 从剪贴板粘贴 ---- */

function emptyRole(): Role | null {
  const hit = ROLE_META.find((r) => !props.images?.[r.role])
  return hit ? hit.role : null
}

async function onPaste(event: ClipboardEvent) {
  const items = Array.from(event.clipboardData?.items || [])
  const image = items.find((i) => i.type.startsWith('image/'))
  if (!image) return // 贴文字时不动它
  const file = image.getAsFile()
  if (!file) return
  event.preventDefault()
  if (uploading.value) return
  const role = emptyRole()
  if (!role) {
    emit('toast', '四张视角都满了，先移除一张再粘贴', 'err')
    return
  }
  await upload(role, file, true)
  emit('toast', `已从剪贴板粘贴到「${ROLE_META.find((r) => r.role === role)?.label}」`)
}

onMounted(() => document.addEventListener('paste', onPaste))
onBeforeUnmount(() => document.removeEventListener('paste', onPaste))

async function remove(role: Role) {
  try {
    await $fetch(`/api/projects/${props.projectId}/images/${role}`, { method: 'DELETE' })
    emit('refresh')
  } catch (err: any) {
    emit('toast', err?.data?.statusMessage || '删除失败', 'err')
  }
}

/* ---- 记住本项目的生成设置 ---- */
// 存在 project.json 里：换项目就是换一套参数，别人新建的项目从默认值开始。
let settingsTimer: ReturnType<typeof setTimeout> | null = null
/** 正在把磁盘上的设置灌进控件：这期间的 watch 不是用户操作，别写回文件 */
let hydratingSettings = true
function persistSettings() {
  if (hydratingSettings) return
  if (settingsTimer) clearTimeout(settingsTimer)
  settingsTimer = setTimeout(() => {
    $fetch(`/api/projects/${props.projectId}/settings`, {
      method: 'POST',
      body: {
        scale: scale.value,
        detail: detailLevel.value,
        craft: craft.value,
        complexity: complexity.value,
        hint: aiCfg.hint,
      },
    }).catch(() => {
      /* 存不下来不影响本次生成 */
    })
  }, 500)
}

watch([scale, detailLevel, craft, complexity, () => aiCfg.hint], persistSettings)

/* ---- AI 配置与出图 ---- */

onMounted(async () => {
  try {
    const cfg = await $fetch<any>('/api/ai/config')
    aiCfg.baseUrl = cfg.baseUrl
    aiCfg.model = cfg.model
    aiCfg.hasKey = cfg.hasKey
    aiCfg.keyHint = cfg.keyHint
    aiCfg.visionBaseUrl = cfg.visionBaseUrl || ''
    aiCfg.visionModel = cfg.visionModel || ''
    aiCfg.hasVisionKey = !!cfg.hasVisionKey
    aiCfg.visionKeyHint = cfg.visionKeyHint || ''
    aiCfg.visionEffective = cfg.visionEffective || ''
    aiCfg.visionOk = cfg.visionOk ?? null
    aiCfg.visionNote = cfg.visionNote || ''
    // 用了单独的视觉模型、或者测出来看不了图，才把这一栏展开
    aiCfg.visionOpen = !!cfg.visionModel || cfg.visionOk === false
  } catch {
    /* 没配置就留空 */
  }
  // 套用这个项目上次的参数（新项目没有 settings，保持默认）
  const st = props.settings
  if (st) {
    if (typeof st.scale === 'number') scale.value = Math.min(SCALE_MAX, Math.max(SCALE_MIN, st.scale))
    if (typeof st.detail === 'number') detailLevel.value = Math.min(4, Math.max(1, Math.round(st.detail)))
    if (typeof st.craft === 'number') craft.value = Math.min(2, Math.max(0, Math.round(st.craft)))
    if (typeof st.complexity === 'number') complexity.value = Math.min(5, Math.max(1, Math.round(st.complexity)))
    if (typeof st.hint === 'string') aiCfg.hint = st.hint
  }
  // 灌完之后放开写入：否则一进项目就把同样的值再写一遍，
  // 改了 mtime 会让「目录指纹」判定为变化、页面无谓重载一次。
  nextTick(() => {
    hydratingSettings = false
  })

  // 顺手拉一份模型清单：填错模型名和"没联网"在界面上长得一样，很难自查
  try {
    const m = await $fetch<any>('/api/ai/models')
    modelList.value = Array.isArray(m?.models) ? m.models : []
  } catch {
    /* 拉不到就不给提示，不影响填写 */
  }
  if (props.running?.id) {
    job.value = props.running
    status.value = 'running'
    log.value = props.running.log || ''
    startTimers()
  }
})

/** 强制重测看图能力：换了模型、或者怀疑结论过期时点。 */
async function probeVision() {
  aiCfg.probing = true
  try {
    const r = await $fetch<any>('/api/ai/vision', { method: 'POST' })
    aiCfg.visionEffective = r.visionEffective || ''
    aiCfg.visionOk = r.visionOk ?? null
    aiCfg.visionNote = r.visionNote || r.probe?.note || r.error || ''
    emit('toast', aiCfg.visionNote || '检测完成', aiCfg.visionOk ? 'ok' : 'err')
  } catch (err: any) {
    emit('toast', err?.data?.statusMessage || '检测失败', 'err')
  } finally {
    aiCfg.probing = false
  }
}

async function saveCfg() {
  try {
    const cfg = await $fetch<any>('/api/ai/config', {
      method: 'POST',
      body: {
        baseUrl: aiCfg.baseUrl,
        model: aiCfg.model,
        apiKey: aiCfg.keyInput,
        vision: {
          baseUrl: aiCfg.visionBaseUrl,
          model: aiCfg.visionModel,
          apiKey: aiCfg.visionKeyInput,
        },
      },
    })
    aiCfg.hasKey = cfg.hasKey
    aiCfg.keyHint = cfg.keyHint
    aiCfg.keyInput = ''
    aiCfg.hasVisionKey = !!cfg.hasVisionKey
    aiCfg.visionKeyHint = cfg.visionKeyHint || ''
    aiCfg.visionKeyInput = ''
    aiCfg.visionEffective = cfg.visionEffective || ''
    aiCfg.visionOk = cfg.visionOk ?? null
    aiCfg.visionNote = cfg.visionNote || ''
    emit(
      'toast',
      cfg.visionModel
        ? `已保存，看图用 ${cfg.visionModel}`
        : `已保存，看图用主模型 ${cfg.model}`,
    )
  } catch (err: any) {
    emit('toast', err?.data?.statusMessage || '保存失败', 'err')
  }
}

async function makePlan() {
  if (!props.images?.front) {
    emit('toast', '先上传一张正视图', 'err')
    return
  }
  xiaocTell('plan-start')
  aiBusy.value = true
  aiError.value = ''
  aiNote.value = ''
  aiVisionDiff.value = null
  planElapsed.value = 0
  if (planTimer) clearInterval(planTimer)
  planTimer = setInterval(() => (planElapsed.value += 1), 1000)
  // 看门狗：服务端两分钟内会收尾，超过四分钟一定是链路出了问题，
  // 与其让按钮永远转下去，不如报个明白。
  const ctrl = new AbortController()
  const watchdog = setTimeout(() => ctrl.abort(), 240_000)
  try {
    const res = await $fetch<any>(`/api/projects/${props.projectId}/ai/plan`, {
      method: 'POST',
      signal: ctrl.signal,
      body: {
        scale: scale.value,
        detail: detailLevel.value,
        craft: craft.value,
        complexity: complexity.value,
        hint: aiCfg.hint,
      },
    })
    aiBrief.value = res.brief
    aiVision.value = res.vision || null
    aiVisionDiff.value = res.visionDiff || null
    aiNote.value = String(res.note || '')
    // 这一轮实际用谁看的图、看得怎么样：写回配置面板，省得用户自己猜
    const vp = res.visionProbe
    if (vp) {
      aiCfg.visionEffective = vp.model || aiCfg.visionEffective
      aiCfg.visionOk = !!vp.ok
      aiCfg.visionNote = String(vp.note || '')
    }
    aiSpec.value = JSON.stringify(res.spec, null, 2)
    aiSeed.value = Number(res.seed) || null
    aiUsage.value = res.usage || null
    aiAnalysis.value = res.analysis || null
    aiScore.value = typeof res.score === 'number' ? res.score : null
    xiaocTell('plan-done', { score: aiScore.value ?? undefined })
    // 照片被裁过：接口会按对称把建筑补全，把"补了多少"带回界面
    const cpt = res.spec?.complete
    aiComplete.value =
      cpt && Array.isArray(cpt.cut) && cpt.cut.length
        ? { cut: cpt.cut.map(String), ratio: Number(cpt.ratio) || 1 }
        : null
    aiRounds.value = Array.isArray(res.rounds) ? res.rounds : []
    // AI 会先判断建筑类型与实际尺寸，存下来给"自动选尺寸"和提示用
    const b = res.spec?.building
    if (b?.meters?.length === 3) {
      buildingMeters.value = b.meters.map(Number)
      buildingType.value = String(b.type || '')
    }
    const m = buildingMeters.value
    const scoreText =
      aiScore.value !== null ? `，体检 ${aiScore.value} 分（第 ${aiRounds.value.length} 轮）` : ''
    const v = aiVision.value
    const visionText = v?.type ? `，看图判读：${v.type}` : ''
    // 看不了图时要说清楚：不是"没配"，而是"这个模型看不见"，否则用户会以为功能坏了
    const blind =
      res.visionProbe && res.visionProbe.ok === false
        ? `\n注意：${res.visionProbe.model} 读不出图，这一轮只用了本地量测（比例/窗阵靠像素算，类型和构件靠猜）。换成 deepseek-flash 这类能看图的模型会准很多。`
        : ''
    const tail = String(res.note || '') || blind
    emit(
      'toast',
      (m
        ? `方案已生成：${buildingType.value || '建筑'} 约 ${m[0]}×${m[1]}×${m[2]} 米${scoreText}${visionText}`
        : `方案已生成${scoreText}${visionText}，检查无误后按方案生成`) + (tail ? `\n${tail}` : ''),
      tail ? 'err' : 'ok',
    )
  } catch (err: any) {
    const aborted = err?.name === 'AbortError' || /aborted|abort/i.test(String(err?.message || ''))
    aiError.value = aborted
      ? '等了 4 分钟还没回来，已中止。可能是网络或 AI 接口太慢，重试一次通常就好。'
      : err?.data?.statusMessage || '生成失败'
    emit('toast', aiError.value, 'err')
  } finally {
    clearTimeout(watchdog)
    if (planTimer) clearInterval(planTimer)
    planTimer = null
    aiBusy.value = false
    if (aiError.value) xiaocTell('plan-fail')
  }
}

async function buildPlan() {
  let spec: any
  try {
    spec = JSON.parse(aiSpec.value)
  } catch {
    emit('toast', '蓝图 JSON 写得不对，先改一下', 'err')
    return
  }
  log.value = ''
  status.value = 'running'
  elapsed.value = 0
  xiaocTell('build-start')
  // 用户手改蓝图时可能把 seed 删掉了：补回来，生成出来的模型才可复现
  if (aiSeed.value && spec.seed == null) spec.seed = aiSeed.value
  try {
    const res = await $fetch<{ job: any }>(`/api/projects/${props.projectId}/ai/build`, {
      method: 'POST',
      body: {
        spec,
        // 一起写进仓库：花了多少 token、这次是怎么分析出来的
        meta: aiUsage.value || aiAnalysis.value
          ? { usage: aiUsage.value, analysis: aiAnalysis.value }
          : null,
      },
    })
    job.value = res.job
    startTimers()
  } catch (err: any) {
    status.value = 'failed'
    xiaocTell('build-fail')
    emit('toast', err?.data?.statusMessage || '启动失败', 'err')
  }
}

function startTimers() {
  stopTimers()
  const began = Date.now()
  tickTimer = setInterval(() => {
    elapsed.value = Math.floor((Date.now() - began) / 1000)
  }, 1000)
  poll()
}

function stopTimers() {
  if (pollTimer) clearTimeout(pollTimer)
  if (tickTimer) clearInterval(tickTimer)
  pollTimer = null
  tickTimer = null
}

async function poll() {
  if (!job.value) return
  try {
    const res = await $fetch<any>(`/api/projects/${props.projectId}/jobs/${job.value.id}`)
    pollFails = 0
    log.value = res.log || ''
    status.value = res.status === 'running' ? 'running' : res.status
    if (res.status === 'running') {
      pollTimer = setTimeout(poll, 700)
      return
    }
    stopTimers()
    if (res.status === 'done') {
      xiaocTell('build-done', { model: res.modelName, elapsed: elapsed.value })
      emit('toast', '生成完成，已跳到输出结果')
      emit('generated', res.modelName)
    } else {
      xiaocTell('build-fail')
      emit('toast', `生成失败（退出码 ${res.code}），日志里能看到原因`, 'err')
    }
  } catch {
    pollFails += 1
    if (pollFails > 30) {
      status.value = 'failed'
      stopTimers()
      emit('toast', '和后台失去联系，任务状态未知，刷新页面看看', 'err')
      return
    }
    pollTimer = setTimeout(poll, 1500)
  }
}

onBeforeUnmount(stopTimers)

function scrollLog() {
  nextTick(() => {
    const el = document.getElementById('run-log')
    if (el) el.scrollTop = el.scrollHeight
  })
}
watch(log, scrollLog)
</script>

<template>
  <div class="input-layout">
    <section class="card">
      <div class="card-head">
        <span class="card-title">视角素材</span>
        <span class="card-note">同一栋楼，最多四张，越多越准</span>
        <div style="flex: 1" />
        <span class="badge" :class="{ brand: imageCount > 0 }">{{ imageCount }} / 4</span>
      </div>
      <div class="card-pad">
        <div class="slot-grid">
          <div
            v-for="meta in ROLE_META"
            :key="meta.role"
            class="slot"
            :class="{ filled: !!images?.[meta.role], dragover: dragRole === meta.role }"
            @click="pick(meta.role)"
            @dragover.prevent="dragRole = meta.role"
            @dragleave="dragRole = null"
            @drop.prevent="onDrop(meta.role, $event)"
          >
            <template v-if="images?.[meta.role]">
              <img :src="images[meta.role]!.url" :alt="meta.label" />
              <div class="slot-shade" />
              <div class="slot-top">
                <span class="slot-role">{{ meta.label }}</span>
                <button class="slot-del" title="移除这张" @click.stop="remove(meta.role)">
                  <UiIcon name="x" :size="12" />
                </button>
              </div>
              <div class="slot-body">
                <div class="slot-name">{{ images[meta.role]!.original || meta.label }}</div>
                <div class="slot-hint">点击可替换</div>
              </div>
            </template>
            <template v-else>
              <div class="slot-empty">
                <span class="plus">
                  <span v-if="uploading === meta.role" class="spinner" />
                  <UiIcon v-else name="plus" :size="16" />
                </span>
                <b>{{ meta.label }}<span v-if="meta.required" style="color: var(--danger)"> *</span></b>
                <small>{{ meta.hint }}</small>
              </div>
            </template>
          </div>
        </div>
        <input ref="fileInput" type="file" accept="image/*" style="display: none" @change="onChange" />
        <div class="fusion-note">
          <UiIcon name="info" :size="14" />
          <span v-if="imageCount > 1">
            将进行<b>多视角融合</b>：正视定立面，侧视量进深，俯视定平面轮廓与屋顶，自由视角校准色偏。
          </span>
          <span v-else>
            只传正视图也能生成，但进深与平面轮廓只能按宽度估算。补一张侧视图，进深就有着落了。
          </span>
          <span style="color: var(--brand-deep); font-weight: 600">也可以直接 Ctrl+V 粘贴截图。</span>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-head">
        <span class="card-title">生成设置</span>
        <span class="card-note">决定交给 AI 的建筑尺度</span>
      </div>
      <div class="card-pad">
        <div class="opt-grid">
          <div class="field" style="grid-column: 1 / -1">
            <label class="field-label" style="display: flex; align-items: center; gap: 8px">
              尺寸
              <b style="color: var(--brand-deep); font-variant-numeric: tabular-nums">
                {{ scale.toFixed(1) }} 格 / 米
              </b>
              <span v-if="scaleHint" style="color: var(--faint); font-weight: 400">
                {{ scaleHint }}
              </span>
              <div style="flex: 1" />
              <button class="btn sm" style="padding: 2px 8px" @click="autoScale">自动</button>
            </label>
            <input
              v-model.number="scale"
              type="range"
              :min="SCALE_MIN"
              :max="SCALE_MAX"
              step="0.5"
              class="scale-slider"
            />
            <div class="scale-ticks">
              <span>0.5 袖珍</span><span>2 默认</span><span>8 超精细</span>
            </div>
          </div>
          <div class="field">
            <label class="field-label">细节等级</label>
            <select v-model.number="detailLevel" class="select">
              <option :value="1">基础（小尺寸）</option>
              <option :value="2">标准</option>
              <option :value="3">精细（大模型）</option>
              <option :value="4">观赏性细节（社区级）</option>
            </select>
          </div>
          <div class="field" style="grid-column: 1 / -1">
            <label class="field-label" style="display: flex; align-items: center; gap: 8px">
              结构数量
              <b style="color: var(--brand-deep)">{{ COMPLEXITY_LABEL[complexity] }}</b>
            </label>
            <input
              v-model.number="complexity"
              type="range"
              min="1"
              max="5"
              step="1"
              class="scale-slider"
            />
            <div class="scale-ticks">
              <span>简易</span><span>基本</span><span>标准</span><span>精细</span><span>复杂</span>
            </div>
            <div class="fusion-note" style="margin-top: 8px">
              <UiIcon name="info" :size="14" />
              <span>
                建筑由几个部分组成：角塔、侧翼、门厅、凸窗、楼梯间、屋面设备。
                照片里的楼通常有几十个组成部分，而 AI 一次只写得动三五个体块，
                所以这一档不让 AI 写 —— 由构建器按主体轮廓往外长。
                <b>调高会让轮廓更碎、方块更多</b>，简易档则是干净的现代体块。
              </span>
            </div>
          </div>
          <div class="field" style="grid-column: 1 / -1">
            <label class="field-label" style="display: flex; align-items: center; gap: 8px">
              游戏建筑技巧性
              <b style="color: var(--brand-deep)">{{ CRAFT_LABEL[craft] }}</b>
            </label>
            <input
              v-model.number="craft"
              type="range"
              min="0"
              max="2"
              step="1"
              class="scale-slider"
            />
            <div class="scale-ticks">
              <span>低 · 贴近照片</span><span>中 · 默认</span><span>高 · 社区法则</span>
            </div>
            <div class="fusion-note" style="margin-top: 8px">
              <UiIcon name="info" :size="14" />
              <span>
                这一档决定 AI 在"还原照片"和"像社区大神的作品"之间怎么取舍：
                调高会优先套用三色原则、开口包框、腰线分段、收分这些形式法则，
                允许改配色和开间节奏；调低则尽量照抄照片的结构与配色。
                <b>层数、比例尺仍以照片为准</b>，层数需要强制指定时写进「补充要求」。
              </span>
            </div>
          </div>
        </div>
        <div v-if="detailLevel >= 4" class="orn-hint">
          <UiIcon name="spark" :size="13" />
          <span>
            在「精细」之上再加一层人味：甲板/屋顶会多出成组的设备箱、天线、救生筒，
            墙面会补窗下外机、花箱、壁灯，并按原图做材质色差（水线、墙脚返潮、窗下挂污）。
            小件<b>不左右对称</b>，位置由种子固定，可复现。方块数会明显增加。
          </span>
        </div>
        <div class="fusion-note" style="margin-top: 12px">
          <UiIcon name="info" :size="14" />
          <span>
            比例尺决定模型多大：AI 先判断建筑类型与实际尺寸（米），再用它换算成格数，
            所以宽高比跟着照片走，不会被拉成长板。它只是偏好，AI 会按建筑常识微调。
          </span>
        </div>
      </div>
    </section>

    <section class="card ai-card">
      <div class="card-head">
        <span class="card-title">AI 生成</span>
        <div style="flex: 1" />
        <span class="badge" :class="{ ok: status === 'done' }">
          {{ status === 'running' ? `运行中 ${elapsed}s` : status === 'done' ? '已完成' : status === 'failed' ? '失败' : '待开始' }}
        </span>
        <span class="badge" :class="{ ok: aiCfg.hasKey }">
          {{ aiCfg.hasKey ? aiCfg.model : '未配置' }}
        </span>
      </div>

      <div class="ai-body">
        <div class="ai-left">
          <div class="param-grid">
            <div class="field">
              <label class="field-label">接口地址</label>
              <input v-model="aiCfg.baseUrl" class="input" placeholder="https://api.deepseek.com/v1" />
            </div>
            <div class="field">
              <label class="field-label">模型</label>
              <input
                v-model="aiCfg.model"
                class="input"
                list="ai-model-list"
                placeholder="deepseek-flash"
              />
              <datalist id="ai-model-list">
                <option v-for="m in modelList" :key="m" :value="m" />
              </datalist>
              <div v-if="modelList.length" class="vision-note">
                {{
                  modelList.includes(aiCfg.model)
                    ? `服务端模型：${modelList.join(' / ')}`
                    : `服务端列的是 ${modelList.join(' / ')}；${aiCfg.model || '当前写法'} 不在清单里，可能是旧别名`
                }}
              </div>
            </div>
            <div class="field">
              <label class="field-label">密钥</label>
              <input
                v-model="aiCfg.keyInput"
                class="input"
                type="password"
                :placeholder="aiCfg.hasKey ? aiCfg.keyHint : 'sk-…'"
              />
            </div>
          </div>
          <div class="field">
            <label class="field-label">补充要求（可选）</label>
            <input v-model="aiCfg.hint" class="input" placeholder="比如：三层带车库，屋顶要坡顶" />
          </div>

          <details class="vision-box" :open="aiCfg.visionOpen" @toggle="aiCfg.visionOpen = ($event.target as any).open">
            <summary>
              看图能力
              <span
                class="vision-state"
                :class="{ on: aiCfg.visionOk === true, off: aiCfg.visionOk === false }"
              >
                {{ visionStateText }}
              </span>
            </summary>
            <div class="vision-hint">
              默认就让主模型自己看图 —— <code>deepseek-chat</code> / <code>deepseek-flash</code>
              都能读图，<code>deepseek-v4-pro</code> 读不了。所以这一栏留空即可；只有想换成通义
              <code>qwen-vl-max</code>、智谱 <code>glm-4v</code>、豆包、Kimi 之类单独的视觉模型时才需要填。
              判断方式不是看文档：给模型发一张写着 <code>K7Q</code> 的小图，读得出来才叫能看图。
            </div>
            <div class="param-grid">
              <div class="field">
                <label class="field-label">接口地址</label>
                <input v-model="aiCfg.visionBaseUrl" class="input" placeholder="留空则跟随上面的接口地址" />
              </div>
              <div class="field">
                <label class="field-label">视觉模型（留空 = 用主模型）</label>
                <input
                  v-model="aiCfg.visionModel"
                  class="input"
                  list="ai-model-list"
                  :placeholder="`当前生效：${aiCfg.visionEffective || aiCfg.model || '—'}`"
                />
              </div>
              <div class="field">
                <label class="field-label">密钥</label>
                <input
                  v-model="aiCfg.visionKeyInput"
                  class="input"
                  type="password"
                  :placeholder="aiCfg.hasVisionKey ? aiCfg.visionKeyHint : '留空则跟随上面的密钥'"
                />
              </div>
            </div>
            <div class="vision-actions">
              <button class="btn" :disabled="aiCfg.probing || !aiCfg.hasKey" @click="probeVision">
                <span v-if="aiCfg.probing" class="spinner" />
                {{ aiCfg.probing ? '检测中' : '重新检测' }}
              </button>
              <span v-if="aiCfg.visionNote" class="vision-note">{{ aiCfg.visionNote }}</span>
            </div>
          </details>

          <div style="display: flex; gap: 8px; flex-wrap: wrap">
            <button class="btn" @click="saveCfg">保存配置</button>
            <button class="btn" :disabled="aiBusy || !images?.front" @click="makePlan">
              <span v-if="aiBusy" class="spinner" />
              {{ aiBusy ? `AI 思考中 ${planElapsed}s` : '让 AI 出方案' }}
            </button>
            <button class="btn primary" :disabled="!aiSpec || status === 'running'" @click="buildPlan">
              {{ status === 'running' ? '正在生成' : '按方案生成' }}
            </button>
          </div>

          <div v-if="aiBusy" class="vision-note" style="margin-top: 2px">
            正在走完整流程：本地量比例 → AI 判读照片 → 写蓝图 → 构建体检 → 修正。
            通常 1~2 分钟（最多 2.5 分钟会自动收尾），期间请不要关闭窗口。
          </div>

          <div style="display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted)">
            <UiIcon name="layers" :size="14" />
            将保存为第 {{ modelCount + 1 }} 个模型
            <div style="flex: 1" />
            <span>{{ progress }}%</span>
          </div>
          <div class="progress">
            <div class="progress-fill" :class="{ done: status === 'done' }" :style="{ width: progress + '%' }" />
          </div>

          <div v-if="aiError" style="font-size: 11.5px; color: var(--err)">{{ aiError }}</div>
          <div v-if="aiVision" class="vision-read">
            <span class="vision-tag">视觉判读</span>
            {{ aiVision.type || aiVision.subject }}
            <template v-if="isShipVision">
              <template v-if="bowLabel"> · 船头朝{{ bowLabel }}</template>
              <template v-if="aiVision.ship?.superstructure">
                · 上建在{{ aiVision.ship.superstructure }}
              </template>
              <template v-if="aiVision.ship?.deck_items?.length">
                · {{ aiVision.ship.deck_items.join('、') }}
              </template>
            </template>
            <template v-else>
              <template v-if="aiVision.floors"> · {{ aiVision.floors }} 层</template>
              <template v-if="aiVision.bays"> · {{ aiVision.bays }} 开间</template>
              <template v-if="aiVision.roof?.type && aiVision.roof.type !== 'none'">
                · 屋顶 {{ aiVision.roof.type }}
              </template>
              <template v-if="aiVision.ornaments?.length">
                · {{ aiVision.ornaments.join('、') }}
              </template>
            </template>
            <template v-if="aiVision.railing?.has"> · 有栏杆</template>
            <template v-if="aiVision.flagpole?.has"> · 有旗杆</template>
          </div>
          <div
            v-if="aiVision && aiVision.subject !== 'building'"
            class="vision-warn"
            :class="{ 'is-ok': isShipVision }"
          >
            <template v-if="isShipVision">
              按舰船语法建模：船长轴沿南北、舰艏在正前方（收窄成尖），甲板栏杆与旗杆按判读位置铺。
            </template>
            <template v-else>
              视觉模型判断主体是「{{ aiVision.subject }}」，不是建筑：模型只能按它的体量近似，
              没有对应的建筑语汇可用。
            </template>
          </div>
          <div v-if="aiNote" class="vision-warn">{{ aiNote }}</div>
          <div v-if="aiVisionDiff?.verdict" class="vision-read">
            <span class="vision-tag alt">看图评价</span>
            {{ aiVisionDiff.verdict }}<template v-if="aiVisionDiff.score">
              （{{ aiVisionDiff.score }} 分）</template
            >
          </div>
          <div v-if="aiBrief" style="font-size: 11px; color: var(--faint)">
            照片分析：体块 {{ aiBrief.massing?.count }} 段 · 窗 {{ aiBrief.windows?.rows }} 排 ×
            {{ aiBrief.windows?.cols }} 列 · 墙色 {{ aiBrief.palette?.wall }}
          </div>

          <div id="run-log" class="log">
            <template v-if="hasLog">
              <span v-for="(line, i) in logLines" :key="i" class="log-line" :class="line.cls">{{ line.text }}</span>
            </template>
            <span v-else class="log-empty">
              日志会实时出现在这里。<br />
              生成过程中会自动跳到「查看输出结果」。
            </span>
          </div>
        </div>

        <div class="ai-right">
          <div class="field">
            <label class="field-label">
              蓝图（可直接改）
              <span v-if="aiSeed" class="seed-badge" :title="'复现码：同一个种子 + 同一份蓝图 = 同一个模型'">
                <UiIcon name="refresh" :size="11" /> 种子 {{ aiSeed }}
              </span>
              <span v-if="aiScore !== null" class="score-badge" :class="{ good: aiScore >= 84 }">
                体检 {{ aiScore }} 分
              </span>
              <span
                v-if="aiComplete"
                class="seed-badge"
                :title="`照片的${aiComplete.cut.map((c) => (c === 'left' ? '左' : '右')).join('、')}边缘被画框切断了，模型按对称补全到 ${aiComplete.ratio} 倍宽`"
              >
                <UiIcon name="sparkle" :size="11" /> 已补全 ×{{ aiComplete.ratio }}
              </span>
            </label>
            <textarea
              v-model="aiSpec"
              class="spec-editor"
              spellcheck="false"
              placeholder="AI 会把蓝图写在这里：volumes / windows / roof / details"
            />
            <div v-if="aiRounds.length > 1" class="round-strip">
              <span
                v-for="r in aiRounds"
                :key="r.round"
                class="round-chip"
                :class="{ best: r.score === aiScore }"
                :title="r.issues.slice(0, 3).join('；')"
              >
                第 {{ r.round }} 轮 {{ r.score }} 分
              </span>
              <span class="round-note">分数低时会自动把问题喂回 AI 再改一版</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
