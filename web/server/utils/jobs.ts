import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { coreCommand, projectDir, rootDir, type Project, type ViewRole } from './store'

export interface JobState {
  id: string
  projectId: string
  modelName: string
  status: 'running' | 'done' | 'failed'
  code: number | null
  startedAt: string
  finishedAt: string | null
  log: string
  command: string
  params: Record<string, any>
}

const jobs = new Map<string, JobState>()
const MAX_LOG = 400_000

const MODES = ['synth', 'building', 'hall', 'relief', 'flat', 'terrain']
const PRESETS = ['xs', 's', 'm', 'l', 'xl']
const NUMERIC = [
  'width', 'depth', 'floors', 'length', 'height', 'bays', 'cell',
  'sat', 'k', 'clean', 'dedupe', 'median', 'strength',
]

function clampNum(key: string, value: unknown): string | null {
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  if (key === 'sat') return String(Math.min(3, Math.max(0.5, n)))
  if (key === 'clean') return String(Math.min(2, Math.max(0, Math.round(n))))
  if (key === 'dedupe') return String(Math.min(40, Math.max(0, n)))
  if (key === 'median') return String(Math.min(31, Math.max(0, Math.round(n))))
  if (key === 'strength') return String(Math.min(20, Math.max(0.5, n)))
  return String(Math.max(0, Math.round(n)))
}

export function buildArgs(project: Project, options: Record<string, any>): string[] {
  const front = project.images.front
  if (!front) {
    throw createError({ statusCode: 400, statusMessage: '还没有上传正视图' })
  }
  const mode = MODES.includes(options.mode) ? String(options.mode) : 'synth'
  const preset = PRESETS.includes(options.preset) ? String(options.preset) : 'm'
  const outDir = path.join(projectDir(project.id), 'output')
  fs.mkdirSync(outDir, { recursive: true })

  const args = [
    '-i', path.join(projectDir(project.id), 'images', front.file),
    '-o', outDir,
    '--name', String(options.name),
    '--mode', mode,
    '--preset', preset,
  ]
  for (const role of ['side', 'top', 'free'] as ViewRole[]) {
    const image = project.images[role]
    if (image) {
      args.push('--view', `${role}=${path.join(projectDir(project.id), 'images', image.file)}`)
    }
  }
  for (const key of NUMERIC) {
    if (options[key] === undefined || options[key] === null || options[key] === '') continue
    const value = clampNum(key, options[key])
    if (value !== null) args.push(`--${key}`, value)
  }
  for (const flag of ['no_glass', 'no_sky', 'no_rect', 'no_flat', 'sym']) {
    if (options[flag]) args.push(`--${flag.replace(/_/g, '-')}`)
  }
  return args
}

export function startJob(project: Project, modelName: string, options: Record<string, any>): JobState {
  const active = activeJob(project.id)
  if (active) {
    throw createError({ statusCode: 409, statusMessage: '这个项目已经有一个任务在跑' })
  }
  const args = buildArgs(project, { ...options, name: modelName })
  const core = coreCommand()
  const fullArgs = [...core.prefix, ...args]
  const id = `j${Date.now().toString(36)}`
  const job: JobState = {
    id,
    projectId: project.id,
    modelName,
    status: 'running',
    code: null,
    startedAt: new Date().toISOString(),
    finishedAt: null,
    log: '',
    command: [core.bin, ...fullArgs].join(' '),
    params: options,
  }
  jobs.set(id, job)

  let child
  try {
    child = spawn(core.bin, fullArgs, {
      cwd: rootDir(),
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
      windowsHide: true,
    })
  } catch (err: any) {
    job.status = 'failed'
    job.log = `无法启动转换进程: ${err?.message || err}\n发布版请确认 core/mc12-core.exe 存在，开发版请确认 python 在 PATH 中。`
    job.finishedAt = new Date().toISOString()
    return job
  }

  const append = (chunk: Buffer) => {
    job.log += chunk.toString('utf-8')
    if (job.log.length > MAX_LOG) job.log = job.log.slice(-MAX_LOG)
  }
  child.stdout?.on('data', append)
  child.stderr?.on('data', append)
  child.on('error', (err: any) => {
    append(Buffer.from(`\n无法启动 Python: ${err?.message || err}\n`))
    job.status = 'failed'
    job.code = -1
    job.finishedAt = new Date().toISOString()
  })
  child.on('close', (code) => {
    job.code = code
    job.status = code === 0 ? 'done' : 'failed'
    job.finishedAt = new Date().toISOString()
  })
  return job
}

export function getJob(id: string): JobState | undefined {
  return jobs.get(id)
}

export function activeJob(projectId: string): JobState | undefined {
  for (const job of jobs.values()) {
    if (job.projectId === projectId && job.status === 'running') return job
  }
  return undefined
}
