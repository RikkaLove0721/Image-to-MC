import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { coreCommand, projectDir, rootDir, type Project } from './store'

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

/** 构建只认蓝图：0.17.0 起照片直转路线已删除 */
export function buildArgs(project: Project, options: Record<string, any>): string[] {
  if (!options.spec) {
    throw createError({ statusCode: 400, statusMessage: '缺少蓝图：现在只支持按蓝图生成' })
  }
  const outDir = path.join(projectDir(project.id), 'output')
  fs.mkdirSync(outDir, { recursive: true })
  return [
    '--spec', String(options.spec),
    '-o', outDir,
    '--name', String(options.name || 'spec'),
  ]
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
    if (code === 0) applyAiMeta(project, modelName, options.meta)
  })
  return job
}

/**
 * 把"这次花了多少 token / 怎么分析出来的"写进仓库。
 *
 * 构建进程（Python）只认蓝图，不知道 AI 花了多少钱，所以这两样由 Node 侧
 * 回填：清单的 params 里存一个精简摘要（界面上要显示），另外落一份
 * `<model>_ai.json` 存完整分析过程 —— 仓库页「修改」要拿它当上下文。
 */
function applyAiMeta(project: Project, modelName: string, meta: any): void {
  if (!meta) return
  const outDir = path.join(projectDir(project.id), 'output')
  const manifestPath = path.join(outDir, `${modelName}_manifest.json`)
  try {
    const m = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'))
    m.params = {
      ...(m.params || {}),
      tokens: meta.usage || null,
      craft: meta.craft ?? null,
      hint: meta.hint ?? null,
      parent: meta.parent || null,
      revLabel: meta.revLabel || null,
    }
    fs.writeFileSync(manifestPath, JSON.stringify(m, null, 2), 'utf-8')
  } catch {
    /* 清单还没写出来就不管：界面顶多少一行 token */
  }
  if (meta.analysis) {
    try {
      fs.writeFileSync(
        path.join(outDir, `${modelName}_ai.json`),
        JSON.stringify(meta.analysis, null, 2),
        'utf-8',
      )
    } catch {
      /* 分析过程存不下不影响模型本身 */
    }
  }
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
