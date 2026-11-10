<script setup lang="ts">
const props = defineProps<{ projectId: string; models: any[] }>()
const emit = defineEmits<{ refresh: []; toast: [msg: string, type: 'ok' | 'err'] }>()

const selectedName = ref<string | null>(null)
const viewKey = ref('iso')

const current = computed(() => props.models.find((m) => m.name === selectedName.value) || props.models[0] || null)

const views = computed(() => {
  if (!current.value) return []
  const urls = current.value.urls || {}
  const out: { key: string; label: string; url: string }[] = []
  if (urls.iso) out.push({ key: 'iso', label: '等轴测外观', url: urls.iso })
  if (urls.cut) out.push({ key: 'cut', label: '剖切内部', url: urls.cut })
  if (urls.preview) out.push({ key: 'preview', label: '配色对比', url: urls.preview })
  return out
})

const stage = computed(() => views.value.find((v) => v.key === viewKey.value) || views.value[0] || null)

watch(
  () => props.models,
  (list) => {
    if (!list.length) {
      selectedName.value = null
      return
    }
    if (!list.some((m) => m.name === selectedName.value)) selectedName.value = list[0].name
  },
  { immediate: true },
)

watch(views, (list) => {
  if (!list.some((v) => v.key === viewKey.value)) viewKey.value = list[0]?.key || 'iso'
})

function select(name: string) {
  selectedName.value = name
  viewKey.value = 'iso'
}

defineExpose({ select })

const blocks = computed(() => {
  const list = current.value?.blocks || []
  return [...list].sort((a: any, b: any) => b.count - a.count)
})

const viewInfo = computed(() => current.value?.manifest?.views || null)

const depthSourceLabel: Record<string, string> = {
  'side+top': '侧视 + 俯视',
  side: '侧视图',
  top: '俯视图',
  guess: '按宽度估算',
}

const percent = (count: number) => {
  const total = current.value?.total || 0
  return total ? ((count / total) * 100).toFixed(1) : '0.0'
}

/**
 * 浏览器里开新标签页, 桌面外壳里交给 pywebview 的内嵌窗口.
 */
async function openViewer() {
  const url = current.value?.urls?.view
  if (!url) return
  const name = current.value?.name || ''
  const ok = await desktopOpenViewer(url, name ? `3D 查看器 · ${name}` : '3D 查看器')
  if (!ok) emit('toast', '打不开 3D 查看器', 'err')
}
</script>

<template>
  <div v-if="!models.length" class="empty" style="padding: 70px 20px">
    <strong>这个项目还没有生成过模型</strong>
    回到「输入工作图片」，传一张正视图，点开始生成。产物会按 m01、m02 的顺序存进这个项目。
    <div style="margin-top: 14px">
      <button class="btn sm ghost" @click="emit('refresh')">
        <UiIcon name="refresh" :size="13" /> 重新扫描产物
      </button>
    </div>
  </div>

  <div v-else class="result-layout">
    <aside>
      <div class="section-head" style="margin-bottom: 8px">
        <span class="section-title" style="font-size: 12.5px; color: var(--muted)">模型 {{ models.length }}</span>
        <div class="section-line" />
        <button class="icon-btn" title="重新扫描产物文件夹" @click="emit('refresh')">
          <UiIcon name="refresh" :size="13" />
        </button>
      </div>
      <div class="model-list">
        <button
          v-for="model in models"
          :key="model.name"
          class="mitem"
          :class="{ active: current?.name === model.name }"
          @click="select(model.name)"
        >
          <span class="mthumb">
            <img v-if="model.urls?.iso" :src="model.urls.iso" :alt="model.name" loading="lazy" />
            <UiIcon v-else name="box" :size="18" />
          </span>
          <span style="min-width: 0">
            <div class="mname">{{ model.name }}</div>
            <div class="mmeta">{{ modeLabel(model.mode) }} · {{ model.size[0] }}×{{ model.size[1] }}×{{ model.size[2] }}</div>
            <div class="mmeta">{{ formatNumber(model.total) }} 块 · {{ model.kinds }} 种</div>
          </span>
        </button>
      </div>
    </aside>

    <section v-if="current" class="card" style="min-width: 0">
      <div class="detail-head">
        <div style="min-width: 0">
          <div class="detail-title">{{ current.name }}</div>
          <div style="font-size: 11.5px; color: var(--faint); margin-top: 3px">
            {{ modeLabel(current.mode) }} · 生成于 {{ formatDateTime(current.createdAt) }}
          </div>
        </div>
        <div style="flex: 1" />
        <span class="badge brand">Java 1.12.2</span>
        <span class="badge">{{ current.manifest?.params?.preset || 'm' }}</span>
        <button class="btn sm ghost" @click="emit('refresh')">
          <UiIcon name="refresh" :size="13" /> 重新扫描
        </button>
      </div>

      <div class="metrics">
        <div class="metric">
          <div class="metric-label">尺寸 X×Y×Z</div>
          <div class="metric-value" style="white-space: nowrap">{{ sizeText(current.size) }}</div>
        </div>
        <div class="metric">
          <div class="metric-label">总方块数</div>
          <div class="metric-value">{{ formatNumber(current.total) }}</div>
        </div>
        <div class="metric">
          <div class="metric-label">方块种类</div>
          <div class="metric-value">{{ current.kinds }}<span class="metric-unit">种</span></div>
        </div>
        <div class="metric">
          <div class="metric-label">参与视角</div>
          <div class="metric-value">{{ (viewInfo?.roles || ['front']).length }}<span class="metric-unit">张</span></div>
        </div>
      </div>

      <div v-if="current.urls?.view" class="viewer-hero">
        <div class="vh-ico"><UiIcon name="box" :size="22" /></div>
        <div class="vh-text">
          <div class="vh-title">3D 查看器</div>
          <div class="vh-note">自由旋转、缩放，逐层查看内部结构</div>
        </div>
        <button class="btn primary" @click="openViewer">
          <UiIcon name="external" :size="14" /> 打开 3D 查看器
        </button>
      </div>

      <div class="viewer">
        <div class="stage">
          <img v-if="stage" :src="stage.url" :alt="stage.label" />
          <div v-else class="stage-empty">
            这个模型没有生成预览图<br />
            命令行里用了 --no-iso 时会这样
          </div>
        </div>
        <div class="view-thumbs">
          <button
            v-for="view in views"
            :key="view.key"
            class="vthumb"
            :class="{ active: stage?.key === view.key }"
            @click="viewKey = view.key"
          >
            <img :src="view.url" :alt="view.label" loading="lazy" />
            <span>{{ view.label }}</span>
          </button>
        </div>
      </div>

      <div class="detail-body">
        <div v-if="viewInfo" class="fusion-note" style="margin: 0 0 14px">
          <UiIcon name="spark" :size="14" />
          <span>
            进深来源 <b>{{ depthSourceLabel[viewInfo.depth_source] || viewInfo.depth_source }}</b>
            · 屋顶 {{ viewInfo.roof_type === 'pitched' ? '坡屋顶倾向' : '平屋顶' }}
            · 平面遮盖 <b>{{ Math.round((viewInfo.footprint_ratio || 1) * 100) }}%</b>
            <template v-if="viewInfo.notes?.length"> · {{ viewInfo.notes.join('；') }}</template>
          </span>
        </div>

        <div class="section-head" style="margin-bottom: 8px">
          <span class="section-title" style="font-size: 13.5px">方块清单</span>
          <span class="section-count">{{ blocks.length }} 种 · 共 {{ formatNumber(current.total) }} 块</span>
          <div class="section-line" />
        </div>
        <table class="block-table">
          <thead>
            <tr>
              <th style="width: 30%">方块</th>
              <th style="width: 16%">数字 ID</th>
              <th style="width: 22%">颜色</th>
              <th style="width: 16%">数量</th>
              <th style="width: 16%">占比</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="block in blocks" :key="`${block.id}:${block.meta}`">
              <td>
                <span class="swatch" :style="{ background: block.hex }" />
                {{ block.name_zh }}
              </td>
              <td style="font-family: var(--mono); color: var(--muted)">{{ block.id }}:{{ block.meta }}</td>
              <td style="font-family: var(--mono); color: var(--muted)">{{ block.hex }}</td>
              <td style="font-weight: 600">{{ formatNumber(block.count) }}</td>
              <td style="color: var(--faint)">{{ percent(block.count) }}%</td>
            </tr>
          </tbody>
        </table>

        <div class="fusion-note" style="margin: 16px 0 0">
          <UiIcon name="info" :size="14" />
          <span>模型文件与方块清单在「查看仓库内容」里按输出结果打包下载。</span>
        </div>
      </div>
    </section>
  </div>
</template>
