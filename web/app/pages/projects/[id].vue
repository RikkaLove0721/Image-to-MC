<script setup lang="ts">
const route = useRoute()
const projectId = String(route.params.id || '')

const { data, pending, refresh, error } = await useAsyncData(
  () => `project-${projectId}`,
  () => $fetch<any>(`/api/projects/${projectId}`),
  { watch: [() => route.params.id] },
)

type TabKey = 'input' | 'result' | 'library'
const router = useRouter()
const initialTab = String(route.query.tab || '')
const tab = ref<TabKey>(
  (['input', 'result', 'library'] as string[]).includes(initialTab) ? (initialTab as TabKey) : 'input',
)

function switchTab(next: TabKey) {
  const changed = next !== tab.value
  tab.value = next
  router.replace({ query: { ...route.query, tab: next } })
  // 切到「输出结果」= 要开始看模型了，让小人接一句话；
  // 切回输入页不打扰（那本来就是常态）
  if (changed && next === 'result') xiaocTell('viewer-open')
}
const resultRef = ref<any>(null)
const toast = ref<{ msg: string; type: 'ok' | 'err' } | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showToast(msg: string, type: 'ok' | 'err' = 'ok') {
  toast.value = { msg, type }
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = null), 2800)
}

const project = computed(() => data.value?.project || null)
const models = computed<any[]>(() => data.value?.models || [])

const images = computed<Record<string, any>>(() => {
  const out: Record<string, any> = {}
  for (const image of data.value?.images || []) {
    const stamp = image.uploadedAt ? new Date(image.uploadedAt).getTime() : ''
    out[image.role] = { ...image, url: `${image.url}?v=${stamp}` }
  }
  return out
})

async function onGenerated(name: string) {
  await refresh()
  switchTab('result')
  await nextTick()
  resultRef.value?.select(name)
}

async function openFromRepo(name: string) {
  switchTab('result')
  await nextTick()
  resultRef.value?.select(name)
}

/* ---- 跟着磁盘上的产物文件夹走 ---- */

const signature = ref('')
let syncTimer: ReturnType<typeof setInterval> | null = null

async function syncFromDisk() {
  /* 任务在跑的时候产物本来就在变, 交给任务轮询, 这里不插一脚 */
  if (data.value?.running) return
  try {
    const res = await $fetch<{ signature: string }>('/api/watch')
    if (!signature.value) {
      signature.value = res.signature
      return
    }
    if (res.signature !== signature.value) {
      signature.value = res.signature
      await refresh()
    }
  } catch {
    /* 忽略本轮 */
  }
}

onMounted(() => {
  void syncFromDisk()
  syncTimer = setInterval(() => void syncFromDisk(), 4000)
})

onBeforeUnmount(() => {
  if (syncTimer) clearInterval(syncTimer)
})

/* ---- 重命名 ---- */

const renameOpen = ref(false)
const renameName = ref('')
const renameBusy = ref(false)

function openRename() {
  if (!project.value) return
  renameName.value = project.value.name
  renameOpen.value = true
  nextTick(() => {
    const el = document.getElementById('proj-rename') as HTMLInputElement | null
    el?.focus()
    el?.select()
  })
}

async function submitRename() {
  const name = renameName.value.trim()
  if (!name) return
  renameBusy.value = true
  try {
    await $fetch(`/api/projects/${projectId}`, { method: 'PATCH', body: { name } })
    renameOpen.value = false
    await refresh()
    showToast('项目已重命名')
  } catch (err: any) {
    showToast(err?.data?.statusMessage || '重命名失败', 'err')
  } finally {
    renameBusy.value = false
  }
}
</script>

<template>
  <div>
    <header class="topbar">
      <div class="topbar-inner">
        <NuxtLink to="/" class="back-link">
          <UiIcon name="arrow-left" :size="15" /> 所有项目
        </NuxtLink>
        <div class="brand" style="margin-left: 4px">
          <div class="brand-mark" style="width: 30px; height: 30px; border-radius: 9px">
            <UiIcon name="box" :size="17" />
          </div>
        </div>
        <div class="proj-title" v-if="project">
          <span>{{ project.name }}</span>
          <span class="badge">创建于 {{ formatDateTime(project.createdAt) }}</span>
          <button class="icon-btn" title="重命名" @click="openRename">
            <UiIcon name="pencil" :size="14" />
          </button>
        </div>
        <div style="flex: 1" />
        <div v-if="project" class="tabs">
          <button class="tab" :class="{ active: tab === 'input' }" @click="switchTab('input')">
            <UiIcon name="upload" :size="14" /> 输入工作图片
          </button>
          <button class="tab" :class="{ active: tab === 'result' }" @click="switchTab('result')">
            <UiIcon name="image" :size="14" /> 查看输出结果
            <span v-if="models.length" class="tab-count">{{ models.length }}</span>
          </button>
          <button class="tab" :class="{ active: tab === 'library' }" @click="switchTab('library')">
            <UiIcon name="grid" :size="14" /> 查看仓库内容
          </button>
        </div>
      </div>
    </header>

    <main class="page">
      <div v-if="pending" style="display: grid; gap: 14px">
        <div class="skeleton" style="height: 210px" />
        <div class="skeleton" style="height: 320px" />
      </div>

      <div v-else-if="error || !project" class="empty" style="padding: 70px 20px">
        <strong>找不到这个项目</strong>
        可能已经在首页删掉了，或者链接不完整。
        <div style="margin-top: 14px">
          <NuxtLink to="/" class="btn sm">回到项目列表</NuxtLink>
        </div>
      </div>

      <template v-else>
        <InputTab
          v-if="tab === 'input'"
          :project-id="projectId"
          :images="images"
          :model-count="models.length"
          :running="data?.running"
          :settings="project?.settings"
          @refresh="refresh()"
          @generated="onGenerated"
          @toast="showToast"
        />
        <ResultTab
          v-else-if="tab === 'result'"
          ref="resultRef"
          :project-id="projectId"
          :models="models"
          @refresh="refresh()"
          @toast="showToast"
        />
        <LibraryTab
          v-else
          :project-id="projectId"
          :models="models"
          @open-result="openFromRepo"
          @refresh="refresh()"
          @toast="showToast"
        />
      </template>

      <AppFooter />
    </main>

    <AppModal :open="renameOpen" title="重命名项目" @close="renameOpen = false">
      <div class="field">
        <label class="field-label">项目名</label>
        <input id="proj-rename" v-model="renameName" class="input" maxlength="40" @keyup.enter="submitRename" />
      </div>
      <template #foot>
        <button class="btn ghost" @click="renameOpen = false">取消</button>
        <button class="btn primary" :disabled="renameBusy" @click="submitRename">保存</button>
      </template>
    </AppModal>

    <Transition name="fade">
      <div v-if="toast" class="toast" :class="{ err: toast.type === 'err' }">
        <UiIcon :name="toast.type === 'err' ? 'info' : 'check'" :size="14" />
        {{ toast.msg }}
      </div>
    </Transition>
  </div>
</template>
