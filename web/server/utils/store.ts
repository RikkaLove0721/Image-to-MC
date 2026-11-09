import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export const VIEW_ROLES = ['front', 'side', 'top', 'free'] as const
export type ViewRole = (typeof VIEW_ROLES)[number]

export interface ViewImage {
  role: ViewRole
  file: string
  original: string
  bytes: number
  uploadedAt: string
}

export interface Project {
  id: string
  name: string
  createdAt: string
  updatedAt: string
  images: Partial<Record<ViewRole, ViewImage>>
  lastModel?: string
}

export interface ModelFile {
  name: string
  label: string
  kind: 'image' | 'schematic' | 'text' | 'html'
  bytes: number
}

export interface ModelSummary {
  name: string
  mode: string
  size: [number, number, number]
  total: number
  kinds: number
  blocks: { id: number; meta: number; name_zh: string; name_en: string; hex: string; count: number }[]
  createdAt: string
  manifest: Record<string, any>
  files: ModelFile[]
  urls: Record<string, string>
}

const ROLE_LABEL: Record<string, string> = {
  iso: '等轴测外观',
  cut: '剖切内部',
  preview: '配色对比',
}

/* 打包成 exe 后 process.pkg 为真, 此时没有源码目录, 一切以 exe 所在位置为准 */
const PACKAGED = Boolean((process as any).pkg)

/** 程序自身所在目录: exe 模式取 exe 目录, 开发模式往上找 run.py。 */
export function appDir(): string {
  const fixed = process.env.MC12_ROOT
  if (fixed && fs.existsSync(fixed)) return fixed
  if (PACKAGED) return path.dirname(process.execPath)
  let dir = process.cwd()
  for (let i = 0; i < 4; i += 1) {
    if (fs.existsSync(path.join(dir, 'run.py'))) return dir
    const parent = path.dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return process.cwd()
}

export const rootDir = appDir

function probeWritable(dir: string): boolean {
  try {
    fs.mkdirSync(dir, { recursive: true })
    fs.accessSync(dir, fs.constants.W_OK)
    return true
  } catch {
    return false
  }
}

/** 项目数据的落盘位置: 环境变量 > exe 同级 projects > 用户本地目录。 */
export function projectsDir(): string {
  const fixed = process.env.MC12_PROJECTS_DIR
  if (fixed) {
    fs.mkdirSync(fixed, { recursive: true })
    return fixed
  }
  const beside = path.join(appDir(), 'projects')
  if (probeWritable(beside)) return beside
  const fallback = path.join(
    process.env.LOCALAPPDATA || path.join(os.homedir(), '.local', 'share'),
    'MC工程图',
    'projects',
  )
  fs.mkdirSync(fallback, { recursive: true })
  return fallback
}

export interface CoreCommand {
  bin: string
  /* bin 前面要带的参数, 例如 python -u run.py 里的 -u 与脚本路径 */
  prefix: string[]
}

/**
 * 转换进程的启动方式. 发布版优先用随包的 core/mc12-core.exe,
 * 找不到才退回系统 Python, 这样用户机器不用装 Python 与 numpy。
 */
export function coreCommand(): CoreCommand {
  const fixed = process.env.MC12_CORE
  if (fixed && fs.existsSync(fixed)) return { bin: fixed, prefix: [] }
  const exe = process.platform === 'win32' ? 'mc12-core.exe' : 'mc12-core'
  for (const dir of [path.join(appDir(), 'core'), path.join(appDir(), '..', 'core')]) {
    const candidate = path.join(dir, exe)
    if (fs.existsSync(candidate)) return { bin: candidate, prefix: [] }
  }
  return {
    bin: process.env.MC12_PYTHON || 'python',
    prefix: ['-u', path.join(appDir(), 'run.py')],
  }
}

export function pythonBin(): string {
  return coreCommand().bin
}

/** 数据目录指纹: 任一项目文件变动都会让签名字符串变化。 */
export function projectsSignature(): string {
  const base = projectsDir()
  const items: string[] = []
  let entries: fs.Dirent[] = []
  try {
    entries = fs.readdirSync(base, { withFileTypes: true })
  } catch {
    return 'empty'
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue
    const dir = path.join(base, entry.name)
    const targets = [path.join(dir, 'project.json'), path.join(dir, 'output'), path.join(dir, 'images')]
    for (const target of targets) {
      try {
        const stat = fs.statSync(target)
        if (stat.isDirectory()) {
          for (const child of fs.readdirSync(target)) {
            const sub = fs.statSync(path.join(target, child))
            items.push(`${entry.name}/${child}:${sub.mtimeMs}:${sub.size}`)
          }
        } else {
          items.push(`${entry.name}:${stat.mtimeMs}:${stat.size}`)
        }
      } catch {
        /* 目录或文件不存在就跳过 */
      }
    }
  }
  items.sort()
  return crypto.createHash('sha1').update(items.join('|')).digest('hex').slice(0, 16)
}

export function safeId(id: string): string {
  if (!/^[a-zA-Z0-9_-]{1,48}$/.test(id)) {
    throw createError({ statusCode: 400, statusMessage: '项目编号不合法' })
  }
  return id
}

export function projectDir(id: string): string {
  return path.join(projectsDir(), safeId(id))
}

function writeJson(file: string, data: unknown) {
  fs.writeFileSync(file, JSON.stringify(data, null, 2), 'utf-8')
}

export function readProject(id: string): Project {
  const dir = projectDir(id)
  const file = path.join(dir, 'project.json')
  if (!fs.existsSync(file)) {
    throw createError({ statusCode: 404, statusMessage: '项目不存在' })
  }
  return JSON.parse(fs.readFileSync(file, 'utf-8')) as Project
}

export function saveProject(project: Project): Project {
  project.updatedAt = new Date().toISOString()
  writeJson(path.join(projectDir(project.id), 'project.json'), project)
  return project
}

export function createProject(name: string): Project {
  const clean = String(name || '').trim().slice(0, 40) || '未命名项目'
  const now = new Date()
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
  ].join('')
  const id = `p${stamp}${Math.random().toString(36).slice(2, 6)}`
  const project: Project = {
    id,
    name: clean,
    createdAt: now.toISOString(),
    updatedAt: now.toISOString(),
    images: {},
  }
  const dir = projectDir(id)
  fs.mkdirSync(path.join(dir, 'images'), { recursive: true })
  fs.mkdirSync(path.join(dir, 'output'), { recursive: true })
  saveProject(project)
  return project
}

export function listProjects(): (Project & { modelCount: number; cover: string | null })[] {
  const base = projectsDir()
  const out: (Project & { modelCount: number; cover: string | null })[] = []
  for (const entry of fs.readdirSync(base, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const file = path.join(base, entry.name, 'project.json')
    if (!fs.existsSync(file)) continue
    try {
      const project = JSON.parse(fs.readFileSync(file, 'utf-8')) as Project
      const models = scanModels(project)
      const latest = models[0]
      out.push({
        ...project,
        modelCount: models.length,
        cover: latest ? (latest.urls.iso || latest.urls.preview || null) : null,
      })
    } catch {
      /* 单个项目文件坏了不该拖垮整个列表 */
    }
  }
  out.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))
  return out
}

export function imagePath(project: Project, role: ViewRole): string | null {
  const image = project.images[role]
  if (!image) return null
  const p = path.join(projectDir(project.id), 'images', image.file)
  return fs.existsSync(p) ? p : null
}

export function fileUrl(projectId: string, file: string): string {
  return `/api/projects/${projectId}/file/${encodeURIComponent(file)}`
}

export function scanModels(project: Project): ModelSummary[] {
  const dir = path.join(projectDir(project.id), 'output')
  if (!fs.existsSync(dir)) return []
  const out: ModelSummary[] = []
  for (const entry of fs.readdirSync(dir)) {
    if (!entry.endsWith('_manifest.json')) continue
    const full = path.join(dir, entry)
    try {
      const manifest = JSON.parse(fs.readFileSync(full, 'utf-8'))
      const name = String(manifest.name || entry.replace('_manifest.json', ''))
      const stamp = Math.round(fs.statSync(full).mtimeMs)
      const files: ModelFile[] = []
      const urls: Record<string, string> = {}
      const add = (suffix: string, key: string, label: string, kind: ModelFile['kind']) => {
        const target = path.join(dir, `${name}${suffix}`)
        if (!fs.existsSync(target)) return
        files.push({ name: `${name}${suffix}`, label, kind, bytes: fs.statSync(target).size })
        /* 带清单时间戳, 同名模型被重新生成或从别处拷进来时不会读到浏览器缓存 */
        urls[key] = `${fileUrl(project.id, `${name}${suffix}`)}?v=${stamp}`
      }
      add('.schematic', 'schematic', '方块模型 .schematic', 'schematic')
      add('_iso.png', 'iso', '等轴测外观', 'image')
      add('_cut.png', 'cut', '剖切内部', 'image')
      add('_preview.png', 'preview', '配色对比', 'image')
      add('_view.html', 'view', '浏览器 3D 查看器', 'html')
      add('_blocks.txt', 'blocks', '方块数量清单', 'text')
      add('_layers.txt', 'layers', '逐层平面图', 'text')

      const created = fs.statSync(full).mtime.toISOString()
      out.push({
        name,
        mode: String(manifest.mode || 'synth'),
        size: (manifest.size || [0, 0, 0]) as [number, number, number],
        total: Number(manifest.total || 0),
        kinds: Number(manifest.kinds || 0),
        blocks: Array.isArray(manifest.blocks) ? manifest.blocks : [],
        createdAt: created,
        manifest,
        files,
        urls,
      })
    } catch {
      /* 忽略损坏的清单 */
    }
  }
  out.sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
  return out
}

export function projectDetail(project: Project) {
  const models = scanModels(project)
  return {
    project,
    models,
    images: VIEW_ROLES.filter((role) => Boolean(project.images[role])).map((role) => ({
      role,
      url: `/api/projects/${project.id}/images/${role}`,
      ...project.images[role],
    })),
  }
}

export function nextModelName(project: Project): string {
  const models = scanModels(project)
  let i = models.length + 1
  const used = new Set(models.map((m) => m.name))
  while (used.has(`m${String(i).padStart(2, '0')}`)) i += 1
  return `m${String(i).padStart(2, '0')}`
}

export { ROLE_LABEL }
