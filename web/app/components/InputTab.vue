<script setup lang="ts">
type Role = 'front' | 'side' | 'top' | 'free'

const props = defineProps<{
  projectId: string
  images: Record<string, { url: string; original?: string; bytes?: number; uploadedAt?: string } | undefined>
  modelCount: number
  running: any
}>()

const emit = defineEmits<{ refresh: []; generated: [name: string]; toast: [msg: string, type?: 'ok' | 'err'] }>()

const ROLE_META: { role: Role; label: string; hint: string; required?: boolean }[] = [
  { role: 'front', label: '正视图', hint: '正对正面，能看清窗阵', required: true },
  { role: 'side', label: '侧视图', hint: '从侧面拍，用来量进深' },
  { role: 'top', label: '俯视图', hint: '俯拍或平面图，定轮廓' },
  { role: 'free', label: '自由视角', hint: '任意角度，校准整体颜色' },
]

const MODES = [
  { value: 'synth', label: '立面合成（推荐）', desc: '照片只提供层数、窗距与配色，立面由文法重新合成，左右对称、窗阵对齐。多视角在这里生效最完整。' },
  { value: 'building', label: '按照片重建', desc: '逐格还原照片里的立面，杂乱也一起保留。适合老墙、不规则立面。' },
  { value: 'hall', label: '室内大厅', desc: '一点透视的室内照（泳池 / 展厅），重建下沉水池、柱廊、壁画与藻井吊顶。' },
  { value: 'relief', label: '浮雕', desc: '把照片压成有起伏的浅浮雕，适合山墙、雕刻、壁画。' },
  { value: 'flat', label: '平板', desc: '最薄的一层像素墙，只做颜色还原，不追求立体。' },
  { value: 'terrain', label: '地貌', desc: '山体、坡地等地形照，按亮度生成高低起伏。' },
]

const PRESETS = [
  { value: 'xs', label: 'xs · 24 格' },
  { value: 's', label: 's · 32 格' },
  { value: 'm', label: 'm · 40 格（默认）' },
  { value: 'l', label: 'l · 56 格' },
  { value: 'xl', label: 'xl · 80 格' },
]

const PARAM_SPEC: Record<string, { key: string; label: string; placeholder: string; hint?: string }[]> = {
  synth: [
    { key: 'depth', label: '进深（格）', placeholder: '留空按视角推断', hint: '多视角时自动取侧视/俯视的估计' },
    { key: 'floors', label: '层数', placeholder: '留空自动识别', hint: '识别不准时手动锁定' },
  ],
  building: [{ key: 'depth', label: '墙体厚度', placeholder: '3', hint: '沿 Z 挤出的格数' }],
  hall: [
    { key: 'length', label: '厅深', placeholder: '自动', hint: '沿 Z 的长度' },
    { key: 'height', label: '净高', placeholder: '自动' },
    { key: 'bays', label: '柱对数', placeholder: '4' },
    { key: 'cell', label: '藻井格边长', placeholder: '5' },
    { key: 'depth', label: '池深', placeholder: '3' },
    { key: 'width', label: '厅宽', placeholder: '跟随预设' },
  ],
  relief: [{ key: 'strength', label: '起伏强度', placeholder: '4', hint: '数值越大越立体' }],
  flat: [],
  terrain: [{ key: 'strength', label: '起伏强度', placeholder: '按亮度', hint: '留空由照片亮度决定' }],
}

const paramsByMode = reactive<Record<string, Record<string, string>>>({
  synth: { depth: '', floors: '' },
  building: { depth: '3' },
  hall: { length: '', height: '', bays: '4', cell: '5', depth: '3', width: '' },
  relief: { strength: '4' },
  flat: {},
  terrain: { strength: '' },
})

const mode = ref('synth')
const preset = ref('m')
const advanced = ref(false)
const adv = reactive({
  sat: '1.5',
  clean: '2',
  dedupe: '10',
  median: '9',
  no_glass: false,
  sym: false,
  no_sky: false,
})

const currentMode = computed(() => MODES.find((m) => m.value === mode.value) || MODES[0])
const params = computed(() => paramsByMode[mode.value] || {})

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
const multiViewMismatch = computed(() => imageCount.value > 1 && mode.value !== 'synth')

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

async function upload(role: Role, file: File) {
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
    emit('toast', `${ROLE_META.find((r) => r.role === role)?.label} 已上传`)
  } catch (err: any) {
    emit('toast', err?.data?.statusMessage || '上传失败', 'err')
  } finally {
    uploading.value = null
  }
}

async function remove(role: Role) {
  try {
    await $fetch(`/api/projects/${props.projectId}/images/${role}`, { method: 'DELETE' })
    emit('refresh')
  } catch (err: any) {
    emit('toast', err?.data?.statusMessage || '删除失败', 'err')
  }
}

function buildPayload() {
  const payload: Record<string, any> = {
    mode: mode.value,
    preset: preset.value,
    sat: adv.sat,
    clean: adv.clean,
    dedupe: adv.dedupe,
    median: adv.median,
    no_glass: adv.no_glass,
    sym: adv.sym,
    no_sky: adv.no_sky,
  }
  for (const [key, value] of Object.entries(params.value)) {
    if (value !== '' && value !== null && value !== undefined) payload[key] = value
  }
  return payload
}

async function start() {
  if (!props.images?.front) {
    emit('toast', '先上传一张正视图', 'err')
    return
  }
  log.value = ''
  status.value = 'running'
  elapsed.value = 0
  try {
    const res = await $fetch<{ job: any }>(`/api/projects/${props.projectId}/generate`, {
      method: 'POST',
      body: buildPayload(),
    })
    job.value = res.job
    startTimers()
  } catch (err: any) {
    status.value = 'failed'
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
      emit('toast', '生成完成，已跳到输出结果')
      emit('generated', res.modelName)
    } else {
      emit('toast', `生成失败（退出码 ${res.code}），日志里能看到原因`, 'err')
    }
  } catch (err) {
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

onMounted(() => {
  if (props.running?.id) {
    job.value = props.running
    status.value = 'running'
    log.value = props.running.log || ''
    startTimers()
  }
})

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
    <div style="display: flex; flex-direction: column; gap: 16px; min-width: 0">
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
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <span class="card-title">重建参数</span>
          <span class="card-note">决定模型长什么样</span>
        </div>
        <div class="card-pad">
          <div class="opt-grid">
            <div class="field">
              <label class="field-label">重建路线</label>
              <select v-model="mode" class="select">
                <option v-for="m in MODES" :key="m.value" :value="m.value">{{ m.label }}</option>
              </select>
            </div>
            <div class="field">
              <label class="field-label">尺寸预设</label>
              <select v-model="preset" class="select">
                <option v-for="p in PRESETS" :key="p.value" :value="p.value">{{ p.label }}</option>
              </select>
            </div>
          </div>
          <div class="mode-desc" style="margin-top: 8px">{{ currentMode.desc }}</div>
          <div v-if="multiViewMismatch" class="fusion-note" style="background: var(--warn-soft); border-color: #f6e2bd">
            <UiIcon name="info" :size="14" />
            <span>
              这条路线只用正视图。想用上侧视 / 俯视的进深与轮廓，请切到
              <b>立面合成</b>。
            </span>
          </div>

          <div class="divider" />

          <div v-if="PARAM_SPEC[mode]?.length" class="param-grid">
            <div v-for="spec in PARAM_SPEC[mode]" :key="spec.key" class="field">
              <label class="field-label">{{ spec.label }}</label>
              <input v-model="paramsByMode[mode][spec.key]" class="input" :placeholder="spec.placeholder" />
              <span v-if="spec.hint" style="font-size: 10.5px; color: var(--faint)">{{ spec.hint }}</span>
            </div>
          </div>
          <div v-else style="font-size: 12px; color: var(--faint)">这条路线没有额外参数，直接生成即可。</div>

          <div class="adv">
            <div class="adv-head" @click="advanced = !advanced">
              <span style="display: inline-flex; align-items: center; gap: 6px">
                <UiIcon name="chevron-down" :size="14" :style="{ transform: advanced ? 'rotate(180deg)' : 'none', transition: '0.2s' }" />
                高级选项
              </span>
              <span style="font-size: 11px; color: var(--faint)">{{ advanced ? '收起' : '色彩与规整强度' }}</span>
            </div>
            <div v-if="advanced" class="adv-body">
              <div class="field">
                <label class="field-label">饱和度增益</label>
                <input v-model="adv.sat" class="input" placeholder="1.5" />
              </div>
              <div class="field">
                <label class="field-label">规整强度</label>
                <select v-model="adv.clean" class="select">
                  <option value="0">0 · 不规整</option>
                  <option value="1">1 · 统一窗格</option>
                  <option value="2">2 · 重排窗阵</option>
                </select>
              </div>
              <div class="field">
                <label class="field-label">同色合并阈值</label>
                <input v-model="adv.dedupe" class="input" placeholder="10" />
              </div>
              <div class="field">
                <label class="field-label">中值去噪核</label>
                <input v-model="adv.median" class="input" placeholder="9" />
              </div>
              <label class="check"><input v-model="adv.no_glass" type="checkbox" /> 不自动替换窗户</label>
              <label class="check"><input v-model="adv.sym" type="checkbox" /> 强制左右对称</label>
              <label class="check"><input v-model="adv.no_sky" type="checkbox" /> 保留天空（不剥离）</label>
            </div>
          </div>
        </div>
      </section>
    </div>

    <aside class="card run-card">
      <div class="card-head">
        <span class="card-title">生成模型</span>
        <div style="flex: 1" />
        <span class="badge" :class="{ ok: status === 'done' }">
          {{ status === 'running' ? '运行中' : status === 'done' ? '已完成' : status === 'failed' ? '失败' : '待开始' }}
        </span>
      </div>
      <div class="card-pad" style="display: flex; flex-direction: column; gap: 12px">
        <div style="display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted)">
          <UiIcon name="layers" :size="14" />
          将保存为第 {{ modelCount + 1 }} 个模型
          <div style="flex: 1" />
          <span v-if="status === 'running'" style="font-family: var(--mono)">{{ elapsed }}s</span>
        </div>

        <button class="btn primary wide" :disabled="status === 'running' || !images?.front" @click="start">
          <span v-if="status === 'running'" class="spinner" />
          <UiIcon v-else name="play" :size="15" />
          {{ status === 'running' ? '正在生成' : '开始生成' }}
        </button>

        <div class="progress">
          <div class="progress-fill" :class="{ done: status === 'done' }" :style="{ width: progress + '%' }" />
        </div>

        <div class="run-meta">
          <span>{{ progress }}%</span>
          <div style="flex: 1" />
          <span v-if="imageCount > 1">{{ imageCount }} 视角融合</span>
          <span v-else>单视角</span>
        </div>

        <div id="run-log" class="log">
          <template v-if="hasLog">
            <span v-for="(line, i) in logLines" :key="i" class="log-line" :class="line.cls">{{ line.text }}</span>
          </template>
          <span v-else class="log-empty">
            日志会实时出现在这里。<br />
            生成过程中会自动跳到「输出结果」。
          </span>
        </div>
      </div>
    </aside>
  </div>
</template>
