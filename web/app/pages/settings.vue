<script setup lang="ts">
/**
 * 设置页：左边两个主选项，右边是对应界面。
 *
 * - 使用说明：从原来的弹窗搬进来，内容不变（HelpSections）
 * - 检测更新：当前版本 / 查最新 / 自动覆盖安装
 *
 * 更新分两步走是刻意的：第一步只下载解压到 `<安装目录>/update/staging`，
 * 第二步才覆盖。任何一步失败，现有安装都不会变。
 */
import { desktopBridge } from '~/utils/desktop'

interface ReleaseInfo {
  current: string
  latest: string | null
  hasUpdate: boolean
  name: string
  notes: string
  publishedAt: string
  size: number
  assetName: string
  releaseUrl: string
  desktop: boolean
  installDir: string
  error?: string
}

interface Progress {
  phase: 'idle' | 'downloading' | 'extracting' | 'verifying' | 'ready' | 'installing' | 'error'
  pct: number
  message: string
  version?: string
  bytes?: number
  files?: number
  error?: string
}

type Tab = 'help' | 'update'

const route = useRoute()
const router = useRouter()
const version = useRuntimeConfig().public.version as string
const desktopMode = useDesktopMode()

const tab = ref<Tab>(route.query.tab === 'update' ? 'update' : 'help')
function pick(next: Tab) {
  tab.value = next
  router.replace({ query: { ...route.query, tab: next } })
}

/* ---------------- 检测更新 ---------------- */

const info = ref<ReleaseInfo | null>(null)
const checking = ref(false)
const checkError = ref('')
const checked = ref(false)

async function runCheck(force = true) {
  if (checking.value) return
  checking.value = true
  checkError.value = ''
  try {
    info.value = await $fetch<ReleaseInfo>('/api/update/check', {
      query: force ? { force: '1' } : {},
    })
    if (info.value?.error) checkError.value = info.value.error
    checked.value = true
  } catch (err: any) {
    checkError.value = err?.data?.statusMessage || err?.message || '检测失败'
  } finally {
    checking.value = false
  }
}

/* ---------------- 下载 → 覆盖 ---------------- */

const prog = ref<Progress>({ phase: 'idle', pct: 0, message: '' })
const starting = ref(false)
const installing = ref(false)
const restarting = ref(false)
const actionError = ref('')
let timer: ReturnType<typeof setInterval> | null = null

function stopPoll() {
  if (timer) clearInterval(timer)
  timer = null
}

async function poll() {
  try {
    const s = await $fetch<Progress>('/api/update/state')
    prog.value = s
    if (s.phase === 'ready' || s.phase === 'error' || s.phase === 'installing') stopPoll()
  } catch {
    /* 服务没了就停，不用刷屏 */
    stopPoll()
  }
}

function startPoll() {
  stopPoll()
  timer = setInterval(poll, 700)
}

async function startDownload() {
  if (starting.value) return
  starting.value = true
  actionError.value = ''
  try {
    prog.value = await $fetch<Progress>('/api/update/apply', { method: 'POST' })
    startPoll()
  } catch (err: any) {
    actionError.value = err?.data?.statusMessage || err?.message || '下载失败'
    prog.value = { phase: 'error', pct: 0, message: '更新失败，安装目录没有被改动', error: actionError.value }
  } finally {
    starting.value = false
  }
}

async function cancel() {
  actionError.value = ''
  try {
    prog.value = await $fetch<Progress>('/api/update/cancel', { method: 'POST' })
  } catch (err: any) {
    actionError.value = err?.data?.statusMessage || '取消失败'
  }
}

async function doInstall() {
  if (installing.value) return
  installing.value = true
  actionError.value = ''
  try {
    await $fetch('/api/update/install', { method: 'POST' })
    // 桌面外壳里让主程序自己退出：脚本正等着这个进程消失才覆盖。
    // 浏览器里没有桥，只能提示用户手动关掉。
    const bridge = desktopBridge() as any
    if (bridge?.quit_app) {
      restarting.value = true
      setTimeout(() => {
        bridge.quit_app().catch(() => {
          restarting.value = false
        })
      }, 700)
    } else {
      actionError.value = '已排好更新脚本，请手动关闭本软件再重新打开（脚本会在退出后自动覆盖并启动）'
      installing.value = false
    }
  } catch (err: any) {
    actionError.value = err?.data?.statusMessage || err?.message || '安装失败'
    installing.value = false
  }
}

/* ---------------- 显示用的小函数 ---------------- */

const busy = computed(
  () => prog.value.phase === 'downloading' || prog.value.phase === 'extracting' || prog.value.phase === 'verifying',
)
const ready = computed(() => prog.value.phase === 'ready')
const failed = computed(() => prog.value.phase === 'error')

function mb(bytes?: number) {
  if (!bytes) return '—'
  return `${(bytes / 1048576).toFixed(1)} MB`
}

function day(iso: string) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** 更新说明只留要点：GitHub 的 markdown 在这里按纯文本展示，去掉多余空行。 */
const notes = computed(() => {
  const raw = info.value?.notes || ''
  return raw
    .split('\n')
    .map((l) => l.replace(/^#{1,6}\s*/, '').replace(/^[-*]\s*/, '· ').trimEnd())
    .filter((l) => l.trim().length > 0)
    .slice(0, 24)
})

/** 发布时间与包大小都可能取不到（接口限流时只读 VERSION 文件），有才显示。 */
const latestMeta = computed(() => {
  const bits: string[] = []
  if (info.value?.publishedAt) bits.push(`发布于 ${day(info.value.publishedAt)}`)
  if (info.value?.size) bits.push(mb(info.value.size))
  return bits.join(' · ')
})

onMounted(() => {
  if (tab.value === 'update') runCheck(false)
})
onUnmounted(stopPoll)

watch(tab, (t) => {
  if (t === 'update' && !checked.value) runCheck(false)
})
</script>

<template>
  <div>
    <header class="topbar">
      <div class="topbar-inner">
        <NuxtLink to="/" class="back-link">
          <UiIcon name="arrow-left" :size="15" /> 所有项目
        </NuxtLink>
        <div class="brand">
          <div class="brand-mark"><UiIcon name="box" :size="20" /></div>
          <div>
            <div class="brand-name">MC 工程图</div>
            <div class="brand-sub">设置</div>
          </div>
        </div>
        <div class="topbar-spacer" />
        <span class="badge">v{{ version }}</span>
        <span v-if="desktopMode" class="badge ok">桌面版</span>
      </div>
    </header>

    <main class="page s-page">
      <aside class="s-nav">
        <button class="s-nav-item" :class="{ active: tab === 'help' }" @click="pick('help')">
          <UiIcon name="info" :size="16" />
          <span>使用说明</span>
        </button>
        <button class="s-nav-item" :class="{ active: tab === 'update' }" @click="pick('update')">
          <UiIcon name="download" :size="16" />
          <span>检测更新</span>
        </button>
      </aside>

      <section class="s-body">
        <!-- ============ 使用说明 ============ -->
        <template v-if="tab === 'help'">
          <div class="s-head">
            <h2>使用说明</h2>
            <p>第一次用先扫这一页：怎么拍照片、三个滑条怎么选、产物怎么导进游戏。</p>
          </div>
          <div class="card s-panel">
            <HelpSections />
          </div>
        </template>

        <!-- ============ 检测更新 ============ -->
        <template v-else>
          <div class="s-head">
            <h2>检测更新</h2>
            <p>从 GitHub 发布页取最新版本。更新只覆盖程序本体，项目、模型、AI 密钥都不会动。</p>
          </div>

          <!-- 1. 当前版本 -->
          <div class="card s-card">
            <div class="s-card-head">
              <div class="s-card-title">当前版本</div>
              <span class="badge">v{{ info?.current || version }}</span>
            </div>
            <div class="s-card-body">
              <div class="kv">
                <span class="k">安装目录</span>
                <code class="v mono">{{ info?.installDir || '读取中…' }}</code>
              </div>
              <div class="kv">
                <span class="k">运行方式</span>
                <span class="v">{{ info?.desktop ? '桌面版（可自动覆盖）' : '浏览器 / 开发模式（只能查看版本）' }}</span>
              </div>
              <div class="kv">
                <span class="k">更新源</span>
                <a class="v link" :href="info?.releaseUrl || 'https://github.com/RikkaLove0721/Image-to-MC/releases'" target="_blank" rel="noreferrer">
                  {{ (info?.releaseUrl || 'github.com/RikkaLove0721/Image-to-MC').replace('https://', '') }}
                  <UiIcon name="external" :size="12" />
                </a>
              </div>
            </div>
          </div>

          <!-- 2. 检测最新版本 -->
          <div class="card s-card">
            <div class="s-card-head">
              <div class="s-card-title">检测最新版本</div>
              <button class="btn sm" :disabled="checking" @click="runCheck(true)">
                <UiIcon name="refresh" :size="14" />
                {{ checking ? '检测中…' : '检测更新' }}
              </button>
            </div>
            <div class="s-card-body">
              <p v-if="checkError" class="s-msg err">{{ checkError }}</p>
              <template v-else-if="!checked">
                <p class="s-msg">点右上角「检测更新」查一下 GitHub 上有没有新版本。</p>
              </template>
              <template v-else-if="info?.hasUpdate">
                <p class="s-msg ok">
                  有新版本 <b>v{{ info.latest }}</b>
                  <span v-if="latestMeta" class="dim">· {{ latestMeta }}</span>
                </p>
                <ul v-if="notes.length" class="notes">
                  <li v-for="(line, i) in notes" :key="i">{{ line }}</li>
                </ul>
                <p v-else class="s-msg dim">取不到发布说明（接口限流），可以直接看发布页。</p>
              </template>
              <p v-else class="s-msg ok">
                已经是最新版本（v{{ info?.current || version }}）
                <span v-if="latestMeta" class="dim">· {{ latestMeta }}</span>
              </p>
            </div>
          </div>

          <!-- 3. 自动更新覆盖 -->
          <div class="card s-card">
            <div class="s-card-head">
              <div class="s-card-title">自动更新覆盖</div>
              <button
                v-if="info?.hasUpdate && !busy && !ready && !failed && prog.phase !== 'installing'"
                class="btn sm primary"
                :disabled="starting"
                @click="startDownload"
              >
                <UiIcon name="download" :size="14" />
                {{ starting ? '准备中…' : '下载并安装' }}
              </button>
            </div>
            <div class="s-card-body">
              <p class="s-msg">
                下载完成后会先解压到安装目录的 <code>update</code> 暂存区，校验通过才覆盖。
                <b>projects / logs / ai.json 不会被覆盖，也不会被删除。</b>
              </p>

              <div v-if="busy || ready || prog.phase === 'installing'" class="prog">
                <div class="bar"><i :style="{ width: `${prog.pct}%` }" /></div>
                <div class="prog-txt">
                  <span>{{ prog.message }}</span>
                  <span class="dim">{{ prog.pct }}%</span>
                </div>
              </div>

              <div v-if="ready" class="s-actions">
                <button class="btn sm primary" :disabled="installing" @click="doInstall">
                  <UiIcon name="check" :size="14" /> 重启并完成更新
                </button>
                <button class="btn sm ghost" @click="cancel">取消，删掉暂存的包</button>
              </div>

              <p v-if="failed" class="s-msg err">
                {{ prog.message }}<br />
                <span class="dim">{{ prog.error }}</span>
              </p>
              <p v-if="actionError" class="s-msg err">{{ actionError }}</p>

              <p v-if="!info?.desktop" class="s-msg dim">
                当前不是从 exe 启动的，覆盖安装只对桌面版生效。开发模式下请用仓库里的
                <code>pack/build_release.py</code> 重新打包。
              </p>
            </div>
          </div>
        </template>
      </section>
    </main>

    <Transition name="fade">
      <div v-if="restarting" class="restart-mask">
        <div class="restart-box">
          <div class="spin" />
          <div class="restart-title">正在覆盖安装</div>
          <div class="restart-desc">
            软件会自己退出，覆盖完成后自动重新打开。<br />
            请稍等十几秒，不要手动结束进程。
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.s-page {
  display: grid;
  grid-template-columns: 208px minmax(0, 1fr);
  gap: 22px;
  align-items: start;
}

/* ---- 左栏 ---- */
.s-nav {
  position: sticky;
  top: 80px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}
.s-nav-item {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 38px;
  padding: 0 11px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
  transition: 0.15s ease;
}
.s-nav-item:hover {
  background: var(--panel-2);
  color: var(--text);
}
.s-nav-item.active {
  background: var(--brand-soft);
  color: var(--brand-deep);
}

/* ---- 右栏 ---- */
.s-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
  max-width: 900px;
}
.s-head h2 {
  margin: 0 0 4px;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: 0.2px;
}
.s-head p {
  margin: 0 0 6px;
  font-size: 12.5px;
  color: var(--muted);
  line-height: 1.7;
}
.s-panel {
  padding: 20px 22px;
}
.s-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 13px 18px;
  border-bottom: 1px solid var(--line-2);
}
.s-card-title {
  font-size: 13.5px;
  font-weight: 700;
}
.s-card-head > .btn {
  margin-left: auto;
}
.s-card-body {
  padding: 14px 18px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.kv {
  display: flex;
  align-items: baseline;
  gap: 12px;
  font-size: 12.5px;
  line-height: 1.8;
}
.kv .k {
  width: 68px;
  flex: none;
  color: var(--faint);
}
.kv .v {
  color: var(--text);
  min-width: 0;
  word-break: break-all;
}
.mono {
  font-family: var(--mono);
  font-size: 12px;
  background: var(--panel-2);
  border: 1px solid var(--line-2);
  border-radius: 6px;
  padding: 1px 6px;
}
.link {
  color: var(--brand-deep);
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.link:hover {
  text-decoration: underline;
}

.s-msg {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--muted);
}
.s-msg b {
  color: var(--text);
}
.s-msg.ok {
  color: var(--ok);
}
.s-msg.ok b {
  color: var(--ok);
}
.s-msg.err {
  color: var(--danger);
}
.s-msg.dim {
  color: var(--faint);
}
.s-msg code {
  background: var(--line);
  padding: 1px 4px;
  border-radius: 4px;
  font-family: var(--mono);
  font-size: 11.5px;
}
.dim {
  color: var(--faint);
}

.notes {
  margin: 2px 0 0;
  padding-left: 18px;
  max-height: 168px;
  overflow: auto;
}
.notes li {
  font-size: 12px;
  line-height: 1.75;
  color: var(--muted);
}

.prog {
  margin-top: 4px;
}
.bar {
  height: 7px;
  border-radius: 99px;
  background: var(--line);
  overflow: hidden;
}
.bar i {
  display: block;
  height: 100%;
  border-radius: 99px;
  background: linear-gradient(90deg, #3b78ff, #2258e0);
  transition: width 0.3s ease;
}
.prog-txt {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--muted);
}

.s-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

/* ---- 重启遮罩 ---- */
.restart-mask {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: grid;
  place-items: center;
  background: rgba(12, 18, 30, 0.62);
  backdrop-filter: blur(3px);
}
.restart-box {
  text-align: center;
  color: #fff;
  padding: 26px 30px;
  border-radius: 16px;
  background: rgba(22, 30, 44, 0.94);
  border: 1px solid rgba(255, 255, 255, 0.12);
}
.spin {
  width: 26px;
  height: 26px;
  margin: 0 auto 14px;
  border-radius: 50%;
  border: 2.5px solid rgba(255, 255, 255, 0.24);
  border-top-color: #6ea0ff;
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.restart-title {
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 6px;
}
.restart-desc {
  font-size: 12.5px;
  line-height: 1.8;
  color: rgba(255, 255, 255, 0.72);
}

@media (max-width: 860px) {
  .s-page {
    grid-template-columns: 1fr;
  }
  .s-nav {
    position: static;
    flex-direction: row;
  }
}
</style>
