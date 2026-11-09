<script setup lang="ts">
const props = defineProps<{ models: any[] }>()

interface Block {
  id: number
  meta: number
  oldId: string
  nameEn: string
  nameZh: string
  hex: string
  rgb: [number, number, number]
  rough: boolean
  semantic: boolean
  category: string
}

const { data } = await useAsyncData('library-data', () => $fetch<any>('/api/library'))
const blocks = computed<Block[]>(() => data.value?.blocks || [])
const categories = computed<string[]>(() => data.value?.categories || [])

const keyword = ref('')
const category = ref('全部')
const paintOnly = ref(false)
const selected = ref<Block | null>(null)

const usage = computed(() => {
  const map = new Map<string, number>()
  for (const model of props.models) {
    for (const block of model.blocks || []) {
      const key = `${block.id}:${block.meta}`
      map.set(key, (map.get(key) || 0) + (block.count || 0))
    }
  }
  return map
})

const filtered = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  return blocks.value.filter((block) => {
    if (paintOnly.value && block.semantic) return false
    if (category.value !== '全部' && block.category !== category.value) return false
    if (!q) return true
    return (
      block.nameZh.includes(q) ||
      block.nameEn.toLowerCase().includes(q) ||
      block.oldId.includes(q) ||
      block.hex.toLowerCase().includes(q)
    )
  })
})

watch(
  filtered,
  (list) => {
    if (!selected.value || !list.some((b) => b.id === selected.value!.id && b.meta === selected.value!.meta)) {
      selected.value = list[0] || null
    }
  },
  { immediate: true },
)

function pick(block: Block) {
  selected.value = block
}

function textOn(rgb: [number, number, number]) {
  const lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255
  return lum > 0.62 ? 'rgba(16,24,38,.62)' : 'rgba(255,255,255,.82)'
}

const usedBy = computed(() => {
  if (!selected.value) return []
  const key = `${selected.value.id}:${selected.value.meta}`
  return props.models
    .map((model) => {
      const hit = (model.blocks || []).find((b: any) => `${b.id}:${b.meta}` === key)
      return hit ? { name: model.name, count: hit.count } : null
    })
    .filter(Boolean) as { name: string; count: number }[]
})
</script>

<template>
  <div class="lib-layout">
    <section class="card" style="min-width: 0">
      <div class="card-head">
        <span class="card-title">方块仓库</span>
        <span class="card-note">Minecraft Java 1.12.2 · 数字 ID 体系</span>
        <div style="flex: 1" />
        <span class="badge">{{ filtered.length }} / {{ blocks.length }}</span>
      </div>
      <div class="card-pad">
        <div class="lib-bar">
          <div class="search">
            <UiIcon name="search" :size="15" />
            <input v-model="keyword" class="input" placeholder="搜中文名、英文 id、数字 ID 或色值" />
          </div>
          <label class="check"><input v-model="paintOnly" type="checkbox" /> 只看可配色</label>
        </div>
        <div class="chips" style="margin-bottom: 12px">
          <button class="chip" :class="{ active: category === '全部' }" @click="category = '全部'">全部</button>
          <button
            v-for="cat in categories"
            :key="cat"
            class="chip"
            :class="{ active: category === cat }"
            @click="category = cat"
          >
            {{ cat }}
          </button>
        </div>

        <div v-if="!filtered.length" class="empty">没有匹配的方块，换个关键字试试。</div>
        <div v-else class="lib-grid">
          <button
            v-for="block in filtered"
            :key="`${block.id}:${block.meta}`"
            class="sw-cell"
            :class="{ active: selected?.id === block.id && selected?.meta === block.meta }"
            @click="pick(block)"
          >
            <div class="sw-color" :style="{ background: block.hex, color: textOn(block.rgb) }">
              {{ block.oldId }}
            </div>
            <div class="sw-name">{{ block.nameZh }}</div>
            <div class="sw-id">{{ block.hex }}</div>
          </button>
        </div>
      </div>
    </section>

    <aside v-if="selected" class="card lib-detail">
      <div class="lib-swatch" :style="{ background: selected.hex }">
        <span>{{ selected.hex }}</span>
      </div>
      <div class="card-pad">
        <div style="font-size: 16px; font-weight: 800">{{ selected.nameZh }}</div>
        <div style="font-size: 11.5px; color: var(--faint); font-family: var(--mono); margin-bottom: 12px">
          {{ selected.nameEn }}
        </div>

        <dl class="kv">
          <dt>数字 ID</dt>
          <dd style="font-family: var(--mono)">{{ selected.oldId }}</dd>
          <dt>RGB</dt>
          <dd style="font-family: var(--mono)">{{ selected.rgb.join(', ') }}</dd>
          <dt>分类</dt>
          <dd>{{ selected.category }}</dd>
          <dt>纹理</dt>
          <dd>{{ selected.rough ? '有颗粒 / 花纹' : '平整纯色' }}</dd>
          <dt>用途</dt>
          <dd>{{ selected.semantic ? '语义方块，只由窗户 / 水 / 灯光规则使用' : '可参与颜色匹配' }}</dd>
          <dt>本项目</dt>
          <dd>
            <b>{{ formatNumber(usage.get(selected.oldId) || 0) }}</b> 块
          </dd>
        </dl>

        <div v-if="usedBy.length" style="margin-top: 14px; border-top: 1px dashed var(--line); padding-top: 10px">
          <div style="font-size: 11px; color: var(--faint); margin-bottom: 6px">出现在这些模型里</div>
          <div v-for="item in usedBy" :key="item.name" style="display: flex; font-size: 12px; gap: 8px; padding: 2px 0">
            <span style="color: var(--muted)">{{ item.name }}</span>
            <span style="flex: 1" />
            <span>{{ formatNumber(item.count) }} 块</span>
          </div>
        </div>

        <div class="fusion-note" style="margin-top: 14px">
          <UiIcon name="info" :size="14" />
          <span>1.12.2 用「id:meta」定位方块，换成 1.13+ 的字符串 ID 会导致导入失败。</span>
        </div>
      </div>
    </aside>
  </div>
</template>
