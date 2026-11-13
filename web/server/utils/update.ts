/**
 * 应用内更新：查 GitHub Release → 下载 → 解压 → 覆盖安装。
 *
 * 三条设计原则，都是为了「别把用户的模型搞丢」：
 *
 * 1. **先暂存，后覆盖**。下载与解压全部落在 `<安装目录>/update/` 里，
 *    校验通过（找得到 runtime/server/index.mjs、版本号对得上）才允许覆盖。
 *    网络断了、包坏了、磁盘满了，都停在 "什么都没变" 的状态。
 * 2. **覆盖交给批处理，不自己干**。运行中的 exe 与 node.exe 是锁着的，
 *    服务端没法自己替换自己：写完脚本就退出，由脚本等进程死透再 robocopy。
 * 3. **用户数据一个字节都不碰**。projects / logs / ai.json / vision-probe.json
 *    在两边都排除：不拷、不删、不覆盖。
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { appDir } from './store'

/** 更新源仓库。换成 fork 只要改这一行。 */
const REPO = 'RikkaLove0721/Image-to-MC'
const API_LATEST = `https://api.github.com/repos/${REPO}/releases/latest`
export const RELEASES_PAGE = `https://github.com/${REPO}/releases`
const UA = 'MC12-Studio-Updater'

/** 查更新结果的缓存时间：GitHub 匿名接口 60 次/小时，别点一下就发一次。 */
const CHECK_TTL = 3 * 60 * 1000

export interface ReleaseInfo {
  current: string
  latest: string | null
  hasUpdate: boolean
  name: string
  notes: string
  publishedAt: string
  /** 发布包字节数，0 表示没读到 */
  size: number
  assetName: string
  /** 只给服务端用：前端点按钮时由后端自己去下 */
  assetUrl: string
  releaseUrl: string
  /** 是否跑在桌面外壳里（浏览器里没有安装目录，不能覆盖） */
  desktop: boolean
  installDir: string
  error?: string
}

export interface UpdateState {
  phase: 'idle' | 'downloading' | 'extracting' | 'verifying' | 'ready' | 'installing' | 'error'
  pct: number
  message: string
  version?: string
  bytes?: number
  files?: number
  error?: string
}

let state: UpdateState = { phase: 'idle', pct: 0, message: '还没开始' }
let busy = false
let cache: { at: number; value: ReleaseInfo } | null = null

export function updateState(): UpdateState {
  return { ...state }
}

/* ---------------- 版本 ---------------- */

function parts(v: string): number[] {
  return String(v)
    .replace(/^v/i, '')
    .split(/[.\-+_]/)
    .map((s) => Number.parseInt(s, 10) || 0)
}

/** 1 = a 新，0 = 一样，-1 = a 旧。 */
export function compareVersion(a: string, b: string): number {
  const x = parts(a)
  const y = parts(b)
  for (let i = 0; i < Math.max(x.length, y.length); i += 1) {
    const d = (x[i] || 0) - (y[i] || 0)
    if (d) return d > 0 ? 1 : -1
  }
  return 0
}

/** 读磁盘上的 VERSION。开发模式下是仓库根，打包后是 exe 同级。 */
export function localVersion(): string {
  for (const p of [path.join(appDir(), 'VERSION'), path.resolve(process.cwd(), 'VERSION')]) {
    try {
      const t = fs.readFileSync(p, 'utf-8').trim()
      if (t) return t
    } catch {
      /* 下一个 */
    }
  }
  return '0.0.0'
}

/**
 * 能不能自动覆盖：必须在桌面外壳里（有 runtime/server/index.mjs）。
 * 开发模式下 appDir 是仓库根，覆盖等于把自己的源码改成发布包，必须挡住。
 */
export function isDesktopInstall(): boolean {
  if (!process.env.MC12_ROOT) return false
  return fs.existsSync(path.join(appDir(), 'runtime', 'server', 'index.mjs'))
}

/* ---------------- 查更新 ---------------- */

export async function checkUpdate(force = false): Promise<ReleaseInfo> {
  const current = localVersion()
  const desktop = isDesktopInstall()
  const base: ReleaseInfo = {
    current,
    latest: null,
    hasUpdate: false,
    name: '',
    notes: '',
    publishedAt: '',
    size: 0,
    assetName: '',
    assetUrl: '',
    releaseUrl: RELEASES_PAGE,
    desktop,
    installDir: appDir(),
  }

  if (!force && cache && Date.now() - cache.at < CHECK_TTL) {
    // hasUpdate 必须按"当前版本"重算：缓存里那份是上一次算的，
    // 覆盖安装完再进来，不重算就会一直说"有新版本"。
    const v = cache.value
    return {
      ...v,
      current,
      desktop,
      installDir: base.installDir,
      hasUpdate: Boolean(v.latest) && compareVersion(v.latest, current) > 0,
    }
  }

  // 主路径：直接读仓库里的 VERSION（raw.githubusercontent 走 CDN，不占 API 配额）。
  // 之前只用 /releases/latest，同一个出口 IP 上一堆人用软件就会吃 403 ——
  // 而"有没有新版本"这件事，一个纯文本文件就够了。
  const raw = await latestFromSources()
  const api = await latestFromApi()

  const latest = raw || api?.latest || null
  if (!latest) {
    return {
      ...base,
      error: api?.error || '连不上 GitHub，检查网络或代理后重试',
    }
  }
  const ver = latest.replace(/^v/i, '')
  const info: ReleaseInfo = {
    ...base,
    latest: ver,
    hasUpdate: compareVersion(ver, current) > 0,
    name: api?.name || `v${ver}`,
    notes: api?.notes || '',
    publishedAt: api?.publishedAt || '',
    size: api?.size || 0,
    // 没有 API 数据时按发布包的命名约定拼（pack_zip.py 的产物名是固定的）
    assetName: api?.assetName || `MC12-Studio-v${ver}-win64.zip`,
    assetUrl:
      api?.assetUrl || `https://github.com/${REPO}/releases/download/v${ver}/MC12-Studio-v${ver}-win64.zip`,
    releaseUrl: api?.releaseUrl || RELEASES_PAGE,
  }
  cache = { at: Date.now(), value: info }
  return info
}

/** 仓库里的 VERSION 文件 = 最新发布版本。取不到返回 null，交给 API 兜底。 */
async function latestFromSources(): Promise<string | null> {
  // 三个源一起问，取**最大的**那个版本号：
  //   - raw.githubusercontent 在部分网络（尤其国内）直接连不通；
  //   - api.github.com 有 60 次/小时的匿名限额，公司出口 IP 很容易被打满；
  //   - jsDelivr 走 CDN，国内通常最快，但会缓存分支内容。
  // 取最大值而不是"第一个成功"，是为了不让某个源的旧缓存把更新藏起来。
  const urls = [
    `https://cdn.jsdelivr.net/gh/${REPO}@latest/VERSION`,
    `https://cdn.jsdelivr.net/gh/${REPO}@main/VERSION?t=${Date.now()}`,
    `https://raw.githubusercontent.com/${REPO}/main/VERSION?t=${Date.now()}`,
  ]
  const got = await Promise.all(
    urls.map(async (url) => {
      try {
        const res = await fetch(url, {
          headers: { 'User-Agent': UA, 'Cache-Control': 'no-cache' },
          signal: AbortSignal.timeout(6000),
        })
        if (!res.ok) return null
        const text = (await res.text()).trim().split('\n')[0].trim()
        return /^\d+(\.\d+)*$/.test(text) ? text : null
      } catch {
        return null
      }
    }),
  )
  const ok = got.filter(Boolean) as string[]
  if (!ok.length) return null
  return ok.sort((a, b) => compareVersion(b, a))[0]
}

/** Release 详情（说明、发布时间、资产大小）。限流或断网都只是"少点信息"，不算失败。 */
async function latestFromApi(): Promise<Partial<ReleaseInfo> & { latest?: string; error?: string } | null> {
  try {
    const res = await fetch(API_LATEST, {
      headers: { 'User-Agent': UA, Accept: 'application/vnd.github+json' },
      signal: AbortSignal.timeout(10000),
    })
    if (!res.ok) return null
    const rel = (await res.json()) as any
    const assets: any[] = Array.isArray(rel.assets) ? rel.assets : []
    // 只认 win64 的 zip；发布页上还有源码包，别下错
    const asset =
      assets.find((a) => /\.zip$/i.test(a.name) && /win-?64|win64|windows/i.test(a.name)) ||
      assets.find((a) => /\.zip$/i.test(a.name)) ||
      null
    return {
      latest: String(rel.tag_name || rel.name || '').replace(/^v/i, ''),
      name: String(rel.name || rel.tag_name || ''),
      notes: String(rel.body || ''),
      publishedAt: String(rel.published_at || ''),
      size: Number(asset?.size || 0),
      assetName: String(asset?.name || ''),
      assetUrl: String(asset?.browser_download_url || ''),
      releaseUrl: String(rel.html_url || RELEASES_PAGE),
    }
  } catch {
    return null
  }
}

/* ---------------- 下载 / 解压 ---------------- */

function updateDir(): string {
  return path.join(appDir(), 'update')
}

async function download(url: string, file: string): Promise<number> {
  // 只从 GitHub 的资产域名下载：更新功能会执行下载下来的程序，
  // 来源必须锁死，不能跟着 release 里写的任意 URL 走。
  const host = new URL(url).hostname
  if (!/(^|\.)github\.com$|(^|\.)githubusercontent\.com$/.test(host)) {
    throw new Error(`更新包地址不是 GitHub（${host}），已拒绝`)
  }
  const res = await fetch(url, {
    headers: { 'User-Agent': UA },
    redirect: 'follow',
    signal: AbortSignal.timeout(10 * 60 * 1000),
  })
  if (!res.ok || !res.body) throw new Error(`下载失败（HTTP ${res.status}）`)
  const total = Number(res.headers.get('content-length') || 0)
  let got = 0
  let lastTick = 0
  const body = Readable.fromWeb(res.body as any)
  body.on('data', (chunk: Buffer) => {
    got += chunk.length
    state.bytes = got
    state.pct = total ? Math.min(98, Math.round((got / total) * 100)) : 0
    state.message = total
      ? `正在下载 ${(got / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB`
      : `正在下载 ${(got / 1048576).toFixed(1)} MB`
    // 进度写得太密只会让前端轮询看到一堆抖动
    if (Date.now() - lastTick > 300) lastTick = Date.now()
  })
  await pipeline(body, fs.createWriteStream(file))
  if (total && got < total * 0.98) {
    throw new Error(`下载不完整（${(got / 1048576).toFixed(1)} MB / ${(total / 1048576).toFixed(1)} MB）`)
  }
  return got
}

function extractZip(zip: string, dst: string): Promise<void> {
  return new Promise((resolve, reject) => {
    // 用 .NET 的 ZipFile 而不是 Expand-Archive：后者在 PS 5.1 上按系统编码解
    // 非 ASCII 文件名，`使用说明.txt` 会变乱码。路径走环境变量，避免引号地狱。
    const ps = spawn(
      'powershell.exe',
      [
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-Command',
        'Add-Type -AssemblyName System.IO.Compression.FileSystem; ' +
          '[IO.Compression.ZipFile]::ExtractToDirectory($env:MC12_ZIP, $env:MC12_DST, [Text.Encoding]::UTF8)',
      ],
      {
        env: { ...process.env, MC12_ZIP: zip, MC12_DST: dst },
        windowsHide: true,
        stdio: ['ignore', 'ignore', 'pipe'],
      },
    )
    let err = ''
    ps.stderr?.on('data', (d) => {
      err += d.toString()
    })
    ps.on('error', (e) => reject(new Error(`解压失败：${e.message}`)))
    ps.on('close', (code) => {
      if (code === 0) resolve()
      else reject(new Error(err.trim().split('\n').slice(-3).join(' ') || `解压失败（exit ${code}）`))
    })
  })
}

/** 发布包里套了一层 `MC工程图/`，按"哪一层有 runtime/server"找根。 */
async function findPayloadRoot(dir: string, depth = 0): Promise<string | null> {
  if (fs.existsSync(path.join(dir, 'runtime', 'server', 'index.mjs'))) return dir
  if (depth >= 3) return null
  const entries = await fsp.readdir(dir, { withFileTypes: true })
  for (const e of entries) {
    if (!e.isDirectory()) continue
    const hit = await findPayloadRoot(path.join(dir, e.name), depth + 1)
    if (hit) return hit
  }
  return null
}

async function countFiles(dir: string): Promise<number> {
  let n = 0
  const walk = async (d: string) => {
    const entries = await fsp.readdir(d, { withFileTypes: true })
    for (const e of entries) {
      if (e.isDirectory()) await walk(path.join(d, e.name))
      else n += 1
    }
  }
  await walk(dir)
  return n
}

/* ---------------- 覆盖安装 ---------------- */

/** 覆盖脚本。**只写 ASCII** —— cmd 的默认代码页可能是 936，UTF-8 的中文注释
 *  会让第二行之后整段解析错位。所有中文内容（路径、exe 名）都从参数进来。 */
const INSTALL_BAT = `@echo off
setlocal EnableExtensions
set "PID=%~1"
set "APP=%~2"
set "STAGE=%~3"
set "EXE=%~4"
set "LOG=%APP%\\logs\\update.log"
if not exist "%APP%\\logs" md "%APP%\\logs" >nul 2>&1
echo [%date% %time%] ---- update start (pid %PID%) ---- >>"%LOG%"

rem 1) give the app 8s to quit gracefully; otherwise kill the whole tree.
rem    a live node.exe keeps runtime\\node.exe locked, so it must go first.
powershell -NoProfile -ExecutionPolicy Bypass -Command "for($i=0;$i -lt 16;$i++){ if(-not (Get-Process -Id %PID% -ErrorAction SilentlyContinue)){ exit 0 }; Start-Sleep -Milliseconds 500 }; exit 1"
if errorlevel 1 (
  echo [%date% %time%] still running, force kill >>"%LOG%"
  taskkill /PID %PID% /T /F >>"%LOG%" 2>&1
)
echo [%date% %time%] app stopped >>"%LOG%"
rem onefile bootloader releases the exe a moment after the python child exits
ping -n 4 127.0.0.1 >nul

rem 2) overwrite program files only. projects / logs / update / local config stay.
robocopy "%STAGE%" "%APP%" /E /R:12 /W:2 /XD "%APP%\\projects" "%APP%\\logs" "%APP%\\update" /XF ai.json vision-probe.json /NFL /NDL /NJH /NJS /NP >>"%LOG%" 2>&1
echo [%date% %time%] robocopy exit %ERRORLEVEL% >>"%LOG%"
if exist "%STAGE%" rd /s /q "%STAGE%" >nul 2>&1

:relaunch
rem 3) always relaunch, even if the copy failed: never leave the user with nothing
start "" "%APP%\\%EXE%"
echo [%date% %time%] relaunched >>"%LOG%"
rd /s /q "%APP%\\update" >nul 2>&1
(goto) 2>nul & del "%~f0"
`

/**
 * 第一步：把新版本下下来，解压到 `<安装目录>/update/staging` 并校验。
 * 这期间旧版本照常能用；失败什么都不改。
 */
export async function prepareUpdate(): Promise<UpdateState> {
  if (busy) {
    throw createError({ statusCode: 409, statusMessage: '已经有一个更新在进行，等它跑完' })
  }
  if (!isDesktopInstall()) {
    throw createError({
      statusCode: 400,
      statusMessage: '当前不是桌面版安装目录，不执行覆盖。请下载发布包解压后用 exe 启动',
    })
  }
  const info = await checkUpdate(true)
  if (info.error) throw createError({ statusCode: 502, statusMessage: info.error })
  if (!info.hasUpdate) throw createError({ statusCode: 400, statusMessage: '已经是最新版本' })
  if (!info.assetUrl) throw createError({ statusCode: 404, statusMessage: '这个版本没有可下载的 win64 压缩包' })

  busy = true
  state = { phase: 'downloading', pct: 0, message: '准备下载…', version: info.latest || '' }
  void (async () => {
    const dir = updateDir()
    try {
      await fsp.rm(dir, { recursive: true, force: true })
      await fsp.mkdir(dir, { recursive: true })
      const zip = path.join(dir, 'download.zip')
      const bytes = await download(info.assetUrl, zip)
      state.phase = 'extracting'
      state.pct = 99
      state.message = '正在解压…'
      const raw = path.join(dir, 'raw')
      await fsp.mkdir(raw, { recursive: true })
      await extractZip(zip, raw)

      state.phase = 'verifying'
      state.message = '正在校验…'
      const payload = await findPayloadRoot(raw)
      if (!payload) throw new Error('压缩包里没找到 runtime/server，可能下错文件了')

      const staged = path.join(dir, 'staging')
      await fsp.rm(staged, { recursive: true, force: true })
      await fsp.rename(payload, staged)
      await fsp.rm(raw, { recursive: true, force: true })
      await fsp.rm(zip, { force: true })

      const files = await countFiles(staged)
      if (files < 20) throw new Error(`解压出来的文件只有 ${files} 个，包不完整`)

      // 包里带的 VERSION 必须比当前新，否则是「拿旧包覆盖新版本」
      let stagedVersion = info.latest || ''
      try {
        stagedVersion = (await fsp.readFile(path.join(staged, 'VERSION'), 'utf-8')).trim()
      } catch {
        /* 没 VERSION 就按 tag 算 */
      }
      if (stagedVersion && compareVersion(stagedVersion, localVersion()) <= 0) {
        throw new Error(`包里的版本是 ${stagedVersion}，不比当前的 ${localVersion()} 新`)
      }

      state = {
        phase: 'ready',
        pct: 100,
        message: `已就绪：v${stagedVersion}`,
        version: stagedVersion,
        bytes,
        files,
      }
    } catch (err: any) {
      await fsp.rm(dir, { recursive: true, force: true }).catch(() => {})
      state = {
        phase: 'error',
        pct: 0,
        message: '更新失败，安装目录没有被改动',
        error: err?.message || String(err),
      }
    } finally {
      busy = false
    }
  })()

  return updateState()
}

/**
 * 第二步：写脚本 → 拉起脚本 → 返回，由前端随后调用 quit_app。
 * 脚本自己等进程退出，所以调用方不用操心时序。
 */
export async function installUpdate(): Promise<{ ok: boolean; exe: string; log: string }> {
  if (state.phase !== 'ready') {
    throw createError({ statusCode: 400, statusMessage: '更新包还没准备好，先点「下载并安装」' })
  }
  const root = appDir()
  const staged = path.join(updateDir(), 'staging')
  if (!fs.existsSync(staged)) {
    throw createError({ statusCode: 400, statusMessage: '暂存目录不见了，重新下载一次' })
  }

  const exe = (await fsp.readdir(root, { withFileTypes: true }))
    .filter((e) => e.isFile() && /\.exe$/i.test(e.name))
    .map((e) => e.name)
    .sort((a, b) => a.length - b.length)[0]
  if (!exe) throw createError({ statusCode: 500, statusMessage: '安装目录里找不到主程序 exe' })

  const bat = path.join(os.tmpdir(), `mc12-update-${Date.now()}.cmd`)
  await fsp.writeFile(bat, INSTALL_BAT, 'latin1')

  // 子进程必须脱离本进程树：外壳退出时会 taskkill /T /F 它自己的子进程，
  // 直接 spawn 的话更新脚本会被一起杀掉。走 cmd /c start 让它成为孤儿。
  const child = spawn(
    process.env.ComSpec || 'cmd.exe',
    ['/c', 'start', '', '/b', bat, String(process.ppid), root, staged, exe],
    { detached: true, stdio: 'ignore', windowsHide: true },
  )
  child.unref()

  state = { ...state, phase: 'installing', message: '正在覆盖安装，软件会自动重启' }
  return { ok: true, exe, log: path.join(root, 'logs', 'update.log') }
}

/** 放弃更新，清掉暂存目录。 */
export async function cancelUpdate(): Promise<UpdateState> {
  if (busy) throw createError({ statusCode: 409, statusMessage: '还在下载，等它跑完' })
  await fsp.rm(updateDir(), { recursive: true, force: true }).catch(() => {})
  state = { phase: 'idle', pct: 0, message: '还没开始' }
  return updateState()
}
