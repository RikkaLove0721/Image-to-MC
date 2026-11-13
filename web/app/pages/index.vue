<script setup lang="ts">
interface ProjectRow {
  id: string
  name: string
  createdAt: string
  updatedAt: string
  modelCount: number
  cover: string | null
  images: Record<string, unknown>
}

const version = useRuntimeConfig().public.version as string

const { data, pending, refresh, error } = await useAsyncData('projects', () =>
  $fetch<{ projects: ProjectRow[] }>('/api/projects'),
)
const { data: library } = await useAsyncData('library', () =>
  $fetch<{ total: number; blocks: any[]; categories: string[] }>('/api/library'),
)
const { data: watchInfo } = await useAsyncData('watch', () =>
  $fetch<{ signature: string; dir: string; core: { bin: string } }>('/api/watch'),
)

const projects = computed<ProjectRow[]>(() => data.value?.projects || [])
const modelTotal = computed(() => projects.value.reduce((sum, p) => sum + (p.modelCount || 0), 0))

/* ---- 方块库：全局的, 跟项目无关, 所以放在母页面 ---- */

const showBlocks = ref(false)
const blockPreview = computed<any[]>(() => (library.value?.blocks || []).slice(0, 26))

/* ---- 与磁盘上的项目文件夹保持同步 ---- */

const signature = ref(watchInfo.value?.signature || '')
const dataDir = computed(() => watchInfo.value?.dir || '')
const syncing = ref(false)
const syncedAt = ref(new Date())
let syncTimer: ReturnType<typeof setInterval> | null = null

async function poll() {
  const res = await $fetch<{ signature: string }>('/api/watch')
  if (res.signature === signature.value) return false
  signature.value = res.signature
  await refresh()
  syncedAt.value = new Date()
  return true
}

async function reload(showBusy = false) {
  if (showBusy) syncing.value = true
  try {
    await refresh()
    await poll()
    syncedAt.value = new Date()
  } catch {
    /* 服务端暂时不可达就等下一轮 */
  } finally {
    syncing.value = false
  }
}

function syncNow() {
  void reload(true)
}

async function openDataDir() {
  try {
    await $fetch('/api/reveal', { method: 'POST', body: {} })
  } catch (err: any) {
    showToast(err?.data?.statusMessage || '打不开文件夹', 'err')
  }
}

onMounted(() => {
  /* 外部往 projects 里拷项目、删项目、跑完一次转换, 这里都会跟着刷新 */
  syncTimer = setInterval(() => {
    void poll().catch(() => {
      /* 忽略本轮 */
    })
  }, 4000)
})

onBeforeUnmount(() => {
  if (syncTimer) clearInterval(syncTimer)
})

const toast = ref<{ msg: string; type: 'ok' | 'err' } | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | null = null
function showToast(msg: string, type: 'ok' | 'err' = 'ok') {
  toast.value = { msg, type }
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = null), 2600)
}

/* ---- 新建 ---- */

const createOpen = ref(false)
const createName = ref('')
const createBusy = ref(false)
const createError = ref('')
const createInput = ref<HTMLInputElement | null>(null)

function openCreate() {
  createName.value = ''
  createError.value = ''
  createOpen.value = true
  nextTick(() => createInput.value?.focus())
}

async function submitCreate() {
  const name = createName.value.trim()
  if (!name) {
    createError.value = '给项目起个名字，比如「红楼校舍」'
    return
  }
  createBusy.value = true
  createError.value = ''
  try {
    const res = await $fetch<{ project: { id: string } }>('/api/projects', {
      method: 'POST',
      body: { name },
    })
    createOpen.value = false
    await navigateTo(`/projects/${res.project.id}`)
  } catch (err: any) {
    createError.value = err?.data?.statusMessage || err?.statusMessage || '创建失败，请重试'
  } finally {
    createBusy.value = false
  }
}

/* ---- 重命名 ---- */

const renameOpen = ref(false)
const renameTarget = ref<ProjectRow | null>(null)
const renameName = ref('')
const renameBusy = ref(false)

function openRename(project: ProjectRow) {
  renameTarget.value = project
  renameName.value = project.name
  renameOpen.value = true
  nextTick(() => {
    const el = document.getElementById('rename-input') as HTMLInputElement | null
    el?.focus()
    el?.select()
  })
}

async function submitRename() {
  const name = renameName.value.trim()
  if (!name || !renameTarget.value) return
  renameBusy.value = true
  try {
    await $fetch(`/api/projects/${renameTarget.value.id}`, { method: 'PATCH', body: { name } })
    renameOpen.value = false
    await refresh()
    showToast('项目已重命名')
  } catch (err: any) {
    showToast(err?.data?.statusMessage || '重命名失败', 'err')
  } finally {
    renameBusy.value = false
  }
}

/* ---- 删除 ---- */

const confirmingId = ref<string | null>(null)
let confirmTimer: ReturnType<typeof setTimeout> | null = null

function askDelete(project: ProjectRow) {
  if (confirmingId.value === project.id) {
    void submitDelete(project)
    return
  }
  confirmingId.value = project.id
  if (confirmTimer) clearTimeout(confirmTimer)
  confirmTimer = setTimeout(() => (confirmingId.value = null), 3000)
}

async function submitDelete(project: ProjectRow) {
  try {
    await $fetch(`/api/projects/${project.id}`, { method: 'DELETE' })
    confirmingId.value = null
    await refresh()
    showToast('项目已删除')
  } catch (err: any) {
    showToast(err?.data?.statusMessage || '删除失败', 'err')
  }
}

function openProject(project: ProjectRow) {
  navigateTo(`/projects/${project.id}`)
}
</script>

<template>
  <div>
    <header class="topbar">
      <div class="topbar-inner">
        <div class="brand">
          <div class="brand-mark"><UiIcon name="box" :size="20" /></div>
          <div>
            <div class="brand-name">MC 工程图</div>
            <div class="brand-sub">PHOTO → MINECRAFT 1.12.2</div>
          </div>
        </div>
        <div class="topbar-spacer" />
        <span class="badge">v{{ version }}</span>
        <span class="badge brand">Java 1.12.2</span>
        <NuxtLink to="/settings" class="btn">
          <UiIcon name="info" :size="15" /> 设置
        </NuxtLink>
        <button class="btn primary" @click="openCreate">
          <UiIcon name="plus" :size="16" /> 新建项目
        </button>
      </div>
    </header>

    <main class="page">
      <section class="hero">
        <div class="hero-main">
          <span class="hero-kicker"><UiIcon name="spark" :size="13" /> 多视角重建</span>
          <h1 class="hero-title">
            把一栋楼的照片，<br />
            堆成一座<em>能走进去</em>的方块建筑。
          </h1>
          <p class="hero-desc">
            每个项目独立存放图片、模型与方块清单。上传正视图、侧视图、俯视图，系统会同时量层数、
            推进深、读平面轮廓，再合成为 Minecraft 1.12.2 的 .schematic。
          </p>
          <div class="hero-actions">
            <button class="btn primary" @click="openCreate">
              <UiIcon name="plus" :size="16" /> 新建项目
            </button>
            <NuxtLink class="btn ghost" to="/settings?tab=help">
              <UiIcon name="info" :size="16" /> 第一次用？看说明
            </NuxtLink>
            <a class="btn ghost" href="#projects">
              <UiIcon name="folder" :size="16" /> 已有项目 {{ projects.length }}
            </a>
          </div>
        </div>

        <div class="hero-side">
          <div class="stat">
            <div class="stat-ico"><UiIcon name="folder" :size="19" /></div>
            <div>
              <div class="stat-num">{{ projects.length }}</div>
              <div class="stat-label">个项目</div>
            </div>
          </div>
          <div class="stat">
            <div class="stat-ico"><UiIcon name="layers" :size="19" /></div>
            <div>
              <div class="stat-num">{{ modelTotal }}</div>
              <div class="stat-label">个已生成模型</div>
            </div>
          </div>
          <div class="stat">
            <div class="stat-ico"><UiIcon name="grid" :size="19" /></div>
            <div>
              <div class="stat-num">{{ library?.total || 94 }}</div>
              <div class="stat-label">种可选方块</div>
            </div>
          </div>
        </div>
      </section>

      <section id="projects">
        <div class="section-head">
          <h2 class="section-title">我的项目</h2>
          <span class="section-count">{{ projects.length }} 个 · 按最近更新排序</span>
          <div class="section-line" />
          <div class="section-tools">
            <span class="sync-note" :title="dataDir">
              <i class="sync-dot" :class="{ busy: syncing }" />
              {{ syncing ? '同步中' : `${formatClock(syncedAt)} 已同步` }}
            </span>
            <button class="btn sm ghost" :title="dataDir" @click="openDataDir">
              <UiIcon name="folder" :size="13" /> 项目文件夹
            </button>
            <button class="btn sm ghost" :disabled="syncing" @click="syncNow">
              <UiIcon name="refresh" :size="13" /> 刷新
            </button>
          </div>
        </div>

        <div v-if="pending && !projects.length" class="project-grid">
          <div v-for="i in 4" :key="i" class="skeleton" style="height: 236px" />
        </div>

        <div v-else-if="error" class="empty">
          <strong>没能读到项目列表</strong>
          服务端似乎没有启动好，刷新一下试试。
        </div>

        <div v-else class="project-grid">
          <button
            v-for="project in projects"
            :key="project.id"
            class="pcard"
            @click="openProject(project)"
          >
            <div class="pcover">
              <img v-if="project.cover" :src="project.cover" :alt="project.name" loading="lazy" />
              <div v-else class="pcover-empty">
                <UiIcon name="image" :size="26" />
                <span>还没有生成模型</span>
              </div>
            </div>
            <div class="pbody">
              <div class="pname">{{ project.name }}</div>
              <div class="pmeta">
                <span>创建于 {{ formatDateTime(project.createdAt) }}</span>
              </div>
              <div class="pmeta">
                <span>{{ project.modelCount }} 个模型</span>
                <i class="pdot" />
                <span>{{ relativeTime(project.updatedAt) }}更新</span>
              </div>
              <div class="pcard-actions" @click.stop>
                <button class="btn sm ghost" @click="openProject(project)">
                  <UiIcon name="external" :size="13" /> 打开
                </button>
                <button class="btn sm ghost" @click="openRename(project)">
                  <UiIcon name="pencil" :size="13" /> 重命名
                </button>
                <button
                  class="btn sm ghost"
                  :class="{ 'confirm-del': confirmingId === project.id }"
                  :style="confirmingId === project.id ? '' : 'margin-left: auto; color: var(--danger)'"
                  @click="askDelete(project)"
                >
                  <template v-if="confirmingId === project.id">确认删除</template>
                  <UiIcon v-else name="trash" :size="13" />
                </button>
              </div>
            </div>
          </button>

          <button class="new-card" @click="openCreate">
            <span class="new-plus"><UiIcon name="plus" :size="20" /></span>
            新建项目
            <span style="font-size: 11.5px; font-weight: 400; color: var(--faint)">
              一个项目 = 一组多视角照片 + 若干模型
            </span>
          </button>
        </div>
      </section>

      <section id="blocks">
        <div class="section-head">
          <h2 class="section-title">方块库</h2>
          <span class="section-count">{{ library?.total || 0 }} 种 · Java 1.12.2 数字 ID 体系</span>
          <div class="section-line" />
          <div class="section-tools">
            <button class="btn sm ghost" @click="showBlocks = !showBlocks">
              <UiIcon :name="showBlocks ? 'chevron-down' : 'grid'" :size="13" />
              {{ showBlocks ? '收起' : '展开浏览' }}
            </button>
          </div>
        </div>

        <BlockLibrary v-if="showBlocks" />
        <div v-else class="blocks-teaser" @click="showBlocks = true">
          <div class="teaser-swatches">
            <span
              v-for="block in blockPreview"
              :key="`${block.id}:${block.meta}`"
              class="teaser-sw"
              :style="{ background: block.hex }"
              :title="`${block.nameZh} ${block.oldId}`"
            />
          </div>
          <div class="teaser-text">
            <b>点开看全部 {{ library?.total || 0 }} 种</b>
            <span>
              混凝土 / 羊毛 / 陶瓦 / 木材 / 石材 / 玻璃与光 / 装饰 —— 都能按颜色匹配，
              也可以查数字 ID 与色值。
            </span>
          </div>
        </div>
      </section>

      <AppFooter />
    </main>

    <AppModal
      :open="createOpen"
      title="新建项目"
      desc="给这栋楼起个名字，之后所有视角与模型都归在它下面"
      @close="createOpen = false"
    >
      <div class="field">
        <label class="field-label">项目名</label>
        <input
          ref="createInput"
          v-model="createName"
          class="input"
          maxlength="40"
          placeholder="例如：红砖校舍 / 海边泳池馆"
          @keyup.enter="submitCreate"
        />
        <span v-if="createError" style="font-size: 11.5px; color: var(--danger)">{{ createError }}</span>
        <span v-else style="font-size: 11.5px; color: var(--faint)">创建日期会自动记录，可在项目页随时改名</span>
      </div>
      <template #foot>
        <button class="btn ghost" @click="createOpen = false">取消</button>
        <button class="btn primary" :disabled="createBusy" @click="submitCreate">
          <span v-if="createBusy" class="spinner" />
          {{ createBusy ? '创建中' : '创建并进入' }}
        </button>
      </template>
    </AppModal>

    <AppModal
      :open="renameOpen"
      title="重命名项目"
      :desc="renameTarget ? `创建于 ${formatDate(renameTarget.createdAt)}` : ''"
      @close="renameOpen = false"
    >
      <div class="field">
        <label class="field-label">项目名</label>
        <input
          id="rename-input"
          v-model="renameName"
          class="input"
          maxlength="40"
          @keyup.enter="submitRename"
        />
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
