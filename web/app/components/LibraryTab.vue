<script setup lang="ts">
/**
 * 仓库内容 = 这个项目的所有输出结果。
 *
 * 每个输出是一个选项卡, 选项卡的预览图就是模型图; 点开之后能看到
 * 这一次输出到底按什么设定跑的 —— 尤其是 AI 蓝图 —— 以及三份产物下载。
 */
const props = defineProps<{ projectId: string; models: any[] }>()
const emit = defineEmits<{
  toast: [msg: string, type?: 'ok' | 'err']
  openResult: [name: string]
  refresh: []
}>()

const isDesktop = useDesktopMode()

const activeName = ref<string | null>(null)

const current = computed(
  () => props.models.find((m) => m.name === activeName.value) || props.models[0] || null,
)

watch(
  () => props.models,
  (list) => {
    if (!list.length) {
      activeName.value = null
      return
    }
    if (!list.some((m) => m.name === activeName.value)) activeName.value = list[0].name
  },
  { immediate: true },
)

const spec = computed<any>(() => current.value?.manifest?.spec || null)
const specText = computed(() => (spec.value ? JSON.stringify(spec.value, null, 2) : ''))

const CLEAN_LABEL: Record<string, string> = { '0': '不规整', '1': '统一窗格', '2': '重排窗阵' }
const DECOR_LABEL: Record<string, string> = {
  off: '关闭',
  scene: '非建筑区域（树叶 / 栅栏）',
  all: '非建筑区域 + 书架',
}
const MASSING_LABEL: Record<string, string> = { auto: '自动（屋顶台阶分块）', off: '单一体块' }
const ROOF_LABEL: Record<string, string> = {
  auto: '平顶压边',
  off: '不加工',
  flat: '平顶压边',
  gable: '双坡屋面',
  hip: '四坡屋面',
}
const MULLION_LABEL: Record<string, string> = { auto: '超过 3 格自动分格', off: '整片玻璃' }
const DETAIL_LABEL: Record<number, string> = { 1: '基础', 2: '标准', 3: '精细' }

/** 本次设定：只列真的用到的项, 让一屏能看完 */
const settings = computed<{ key: string; label: string; value: string }[]>(() => {
  const m = current.value
  if (!m) return []
  const p = m.manifest?.params || {}
  // 老版本（0.15 及以前）的清单没有 settings，退回 params 里的同名/近似字段
  const s = p.settings || p
  const rows: { key: string; label: string; value: string }[] = []

  rows.push({ key: 'mode', label: '生成方式', value: modeLabel(m.mode) })
  if (m.mode === 'spec') {
    const sp = m.manifest?.spec || {}
    const lv = Number(sp.detail || 1)
    // 建筑判断 + 比例尺：这两个决定了模型为什么是现在这个大小
    const bld = sp.building
    if (bld?.meters?.length === 3) {
      const [wall, wh, wd] = bld.meters.map(Number)
      const raw = Array.isArray(bld.raw) && bld.raw.length === 3 ? bld.raw.map(Number) : null
      rows.push({
        key: 'building',
        label: '建筑判断',
        value:
          `${bld.type || '建筑'} ${wall}×${wh}×${wd} 米` +
          (raw ? `（AI 原估 ${raw[0]}×${raw[1]}×${raw[2]}，按层数修正）` : ''),
      })
      if (sp.target_width && wall > 0) {
        rows.push({
          key: 'scale',
          label: '比例尺',
          value: `${(sp.target_width / wall).toFixed(1)} 格/米`,
        })
      }
    }
    rows.push({ key: 'detail', label: '细节等级', value: DETAIL_LABEL[lv] || String(lv) })
    rows.push({ key: 'volumes', label: '体块', value: `${p.volumes ?? '-'} 个` })
    rows.push({ key: 'roof', label: '屋顶', value: ROOF_LABEL[p.roof] || p.roof || '-' })
    rows.push({ key: 'details', label: '细节构件', value: `${p.detail_blocks ?? 0} 块` })
  } else {
    if (s.preset) rows.push({ key: 'preset', label: '尺寸预设', value: String(s.preset) })
    if (s.depth) rows.push({ key: 'depth', label: '进深', value: `${s.depth} 格` })
    if (s.clean !== undefined) {
      rows.push({ key: 'clean', label: '规整强度', value: CLEAN_LABEL[String(s.clean)] || String(s.clean) })
    }
    if (s.decor) rows.push({ key: 'decor', label: '装饰替代', value: DECOR_LABEL[s.decor] || s.decor })
    if (s.massing) rows.push({ key: 'massing', label: '体块组合', value: MASSING_LABEL[s.massing] || s.massing })
    if (s.roof) rows.push({ key: 'roof', label: '屋顶收头', value: ROOF_LABEL[s.roof] || s.roof })
    if (s.roof === 'gable' || s.roof === 'hip') {
      rows.push({ key: 'roof_height', label: '屋面高度', value: `${s.roof_height || 3} 格` })
    }
    if (s.interior === 'on') rows.push({ key: 'interior', label: '室内楼板', value: '已铺' })
    if (s.sym) rows.push({ key: 'sym', label: '左右对称', value: '强制' })
    if (s.views) rows.push({ key: 'views', label: '参与视角', value: `${s.views} 张` })
  }
  // token 花费：只有走过 AI 方案的才有；直接按蓝图生成的显示"—"
  const tk = p.tokens
  if (tk) {
    const total = Number(tk.total || 0)
    rows.push({
      key: 'tokens',
      label: 'Token 花费',
      value: total
        ? `${formatNumber(total)}（输入 ${formatNumber(tk.prompt || 0)} / 输出 ${formatNumber(tk.completion || 0)}）`
        : '未统计',
    })
  }
  if (p.parent) rows.push({ key: 'parent', label: '基于', value: String(p.parent) })
  return rows
})

async function onDownload(event: MouseEvent, url?: string, filename?: string) {
  if (!url || !isDesktop.value) return
  event.preventDefault()
  const name = filename || filenameOf(url)
  const full = `${url}${url.includes('?') ? '&' : '?'}download=1`
  const state = await desktopSave(full, name)
  if (state === 'saved') emit('toast', `已保存 ${name}`, 'ok')
  else if (state === 'failed') emit('toast', '保存失败, 换个位置再试', 'err')
}

/** 离线预览页：一个 HTML 文件，3D 引擎也内联在里面，发给朋友双击就能看。 */
const shareUrl = computed(() =>
  current.value ? `/api/projects/${props.projectId}/share?name=${encodeURIComponent(current.value.name)}` : '',
)
const shareName = computed(() => (current.value ? `${current.value.name}-预览.html` : '模型预览.html'))

async function copySpec() {
  if (!specText.value) return
  try {
    await navigator.clipboard.writeText(specText.value)
    emit('toast', '蓝图已复制')
  } catch {
    emit('toast', '浏览器不让写剪贴板，手动选中复制吧', 'err')
  }
}

const deleting = ref(false)

/* ---------------- 修改：带上下文重新生成 ---------------- */
const reviseOpen = ref(false)
const reviseText = ref('')
const revising = ref(false)
const reviseNote = ref('')

function openRevise() {
  reviseText.value = ''
  reviseNote.value = ''
  reviseOpen.value = true
}

async function submitRevise() {
  const m = current.value
  if (!m || revising.value) return
  const fb = reviseText.value.trim()
  if (!fb) {
    emit('toast', '先说一句哪里不满意', 'err')
    return
  }
  revising.value = true
  reviseNote.value = '正在按你的意见改蓝图…'
  try {
    const res = await $fetch<any>(`/api/projects/${props.projectId}/ai/revise`, {
      method: 'POST',
      body: { model: m.name, feedback: fb },
    })
    const jobId = res?.job?.id
    const mk = res?.job?.modelName || ''
    reviseNote.value = `已生成蓝图，正在建模（${mk}）…`
    await pollRevise(jobId)
    reviseOpen.value = false
    revising.value = false
    emit('toast', `已生成 ${mk}，可在仓库里继续改`, 'ok')
    emit('refresh')
  } catch (err: any) {
    revising.value = false
    reviseNote.value = ''
    emit('toast', err?.data?.statusMessage || '修改失败', 'err')
  }
}

/** 等这次构建跑完再刷新仓库，否则列表里还看不到新模型 */
async function pollRevise(jobId: string) {
  if (!jobId) return
  for (let i = 0; i < 600; i++) {
    await new Promise((r) => setTimeout(r, 700))
    try {
      const st = await $fetch<any>(`/api/projects/${props.projectId}/jobs/${jobId}`)
      if (st?.status === 'running') continue
      if (st?.status === 'failed') throw new Error((st?.log || '').slice(-200) || '建模失败')
      return
    } catch (e: any) {
      if (String(e?.message || '').includes('建模失败')) throw e
    }
  }
}

/** 删掉一次输出：产物文件、蓝图、清单一起删，不可撤销。 */
async function removeModel() {
  const m = current.value
  if (!m || deleting.value) return
  const ok = window.confirm(
    `删除「${m.name}」？\n\n` +
      `这会一并删掉它的 .schematic、等轴测图、方块清单、逐层图和 AI 蓝图，\n` +
      `文件直接从磁盘移除，无法撤销。`,
  )
  if (!ok) return
  deleting.value = true
  try {
    const res = await $fetch<{ removed: string[] }>(
      `/api/projects/${props.projectId}/models/${encodeURIComponent(m.name)}`,
      { method: 'DELETE' },
    )
    emit('toast', `已删除 ${m.name}（${res.removed?.length || 0} 个文件）`, 'ok')
    activeName.value = null
    emit('refresh')
  } catch (err: any) {
    emit('toast', err?.data?.statusMessage || '删除失败', 'err')
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div v-if="!models.length" class="empty" style="padding: 70px 20px">
    <strong>仓库里还没有东西</strong>
    每生成一次模型，这里就多一个选项卡。选项卡上是模型图，点开能回看当时的设定、AI 蓝图和产物下载。
  </div>

  <div v-else class="repo">
    <!-- 每个输出结果一个选项卡，预览图就是模型图 -->
    <div class="repo-tabs">
      <button
        v-for="model in models"
        :key="model.name"
        class="repo-tab"
        :class="{ active: current?.name === model.name }"
        @click="activeName = model.name"
      >
        <span class="repo-thumb">
          <img v-if="model.urls?.iso" :src="model.urls.iso" :alt="model.name" loading="lazy" />
          <UiIcon v-else name="box" :size="18" />
        </span>
        <span class="repo-tab-text">
          <b>{{ model.name }}</b>
          <small>
            {{ model.manifest?.params?.revLabel || modeLabel(model.mode) }} ·
            {{ formatNumber(model.total) }} 块
          </small>
        </span>
      </button>
    </div>

    <section v-if="current" class="card repo-detail">
      <div class="detail-head">
        <div style="min-width: 0">
          <div class="detail-title">{{ current.name }}</div>
          <div style="font-size: 11.5px; color: var(--faint); margin-top: 3px">
            {{ modeLabel(current.mode) }} · {{ formatDateTime(current.createdAt) }} ·
            {{ sizeText(current.size) }} · {{ formatNumber(current.total) }} 块 / {{ current.kinds }} 种
          </div>
        </div>
        <div style="flex: 1" />
        <button class="btn sm" @click="emit('openResult', current.name)">
          <UiIcon name="image" :size="13" /> 在查看结果里打开
        </button>
        <button class="btn sm" :disabled="!current.urls?.spec" @click="openRevise">
          <UiIcon name="spark" :size="13" /> 修改
        </button>
        <button class="btn sm danger" :disabled="deleting" @click="removeModel">
          <UiIcon name="trash" :size="13" /> {{ deleting ? '删除中' : '删除' }}
        </button>
      </div>

      <div class="repo-body">
        <div class="repo-preview">
          <img v-if="current.urls?.iso" :src="current.urls.iso" :alt="current.name" />
          <div v-else class="stage-empty">这个模型没有等轴测预览图</div>
        </div>

        <div class="repo-side">
          <div class="repo-block">
            <div class="repo-block-head">本次设定</div>
            <dl class="kv">
              <template v-for="row in settings" :key="row.key">
                <dt>{{ row.label }}</dt>
                <dd>{{ row.value }}</dd>
              </template>
            </dl>
          </div>

          <div class="repo-block">
            <div class="repo-block-head">下载</div>
            <div class="dl-list">
              <a
                v-if="current.urls?.schematic"
                class="btn sm wide"
                :href="current.urls.schematic + '?download=1'"
                @click="onDownload($event, current.urls.schematic)"
              >
                <UiIcon name="download" :size="13" /> 方块模型 .schematic
              </a>
              <a
                v-if="current.urls?.blocks"
                class="btn sm wide"
                :href="current.urls.blocks + '?download=1'"
                @click="onDownload($event, current.urls.blocks)"
              >
                <UiIcon name="download" :size="13" /> 方块数量清单 .txt
              </a>
              <a
                v-if="current.urls?.layers"
                class="btn sm wide"
                :href="current.urls.layers + '?download=1'"
                @click="onDownload($event, current.urls.layers)"
              >
                <UiIcon name="download" :size="13" /> 逐层平面图 .txt
              </a>
              <a
                v-if="current.urls?.spec"
                class="btn sm wide"
                :href="current.urls.spec + '?download=1'"
                @click="onDownload($event, current.urls.spec)"
              >
                <UiIcon name="download" :size="13" /> AI 蓝图 .json
              </a>
              <a
                v-if="shareUrl"
                class="btn sm wide"
                :href="shareUrl"
                :download="shareName"
                @click="onDownload($event, shareUrl, shareName)"
              >
                <UiIcon name="download" :size="13" /> 模型预览 .html
              </a>
            </div>
            <div v-if="shareUrl" class="share-note">
              单个 HTML 文件，3D 引擎已内联：发给朋友双击就能转着看，不用装软件、不用联网。
            </div>
            <div v-if="!current.urls?.spec" style="font-size: 11px; color: var(--faint); margin-top: 8px">
              这个模型是照片直转的，没有 AI 蓝图。
            </div>
          </div>
        </div>
      </div>

      <div class="repo-spec" v-if="spec">
        <div class="section-head" style="margin-bottom: 8px">
          <span class="section-title" style="font-size: 13.5px">输出时的 AI 蓝图</span>
          <span class="section-count">
            {{ spec.volumes?.length || 0 }} 个体块 · {{ spec.windows?.length || 0 }} 组窗阵 ·
            {{ spec.details?.length || 0 }} 处细节
          </span>
          <div class="section-line" />
          <button class="btn sm ghost" @click="copySpec">复制</button>
        </div>
        <pre class="spec-pre">{{ specText }}</pre>
        <div class="spec-note">
          蓝图可以直接改完再生成：
          <code>{{ current.name }}_spec.json</code> 放在项目目录，用
          <code>run.py --spec</code> 重建，或者贴回生成页的蓝图框。
        </div>
      </div>
    </section>

    <AppModal
      :open="reviseOpen"
      title="修改这一版"
      desc="AI 会带着上一版的分析过程和蓝图来改，比例、层数、种子都不动"
      width="520px"
      @close="revising ? null : (reviseOpen = false)"
    >
      <div style="font-size: 12.5px; color: var(--muted); margin-bottom: 8px">
        基于 <b>{{ current?.name }}</b>。说清楚哪里不满意就行，例如：
        <i>二层窗户太多、招牌太小、右侧太空、入口想要台阶</i>。
      </div>
      <textarea
        v-model="reviseText"
        class="input"
        rows="5"
        style="resize: vertical; font-family: inherit; line-height: 1.6"
        placeholder="这一版哪里不对？想改成什么样？"
        :disabled="revising"
      />
      <div v-if="reviseNote" style="font-size: 12px; color: var(--brand-deep); margin-top: 8px">
        {{ reviseNote }}
      </div>
      <template #foot>
        <button class="btn sm ghost" :disabled="revising" @click="reviseOpen = false">取消</button>
        <button class="btn sm primary" :disabled="revising" @click="submitRevise">
          <UiIcon name="spark" :size="13" /> {{ revising ? '生成中…' : '生成修改版' }}
        </button>
      </template>
    </AppModal>
  </div>
</template>
