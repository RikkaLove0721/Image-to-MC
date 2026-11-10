import { spawnSync } from 'node:child_process'
import { coreCommand } from './store'

/** 同步调一次后端（打包后是 mc12-core.exe，开发时是 python run.py）。 */
export function runCore(args: string[], timeoutMs = 180_000) {
  const core = coreCommand()
  const res = spawnSync(core.bin, [...core.prefix, ...args], {
    encoding: 'utf-8',
    timeout: timeoutMs,
    maxBuffer: 32 * 1024 * 1024,
    windowsHide: true,
  })
  return {
    ok: res.status === 0,
    status: res.status,
    stdout: res.stdout || '',
    stderr: res.stderr || '',
  }
}

/** 调后端并解析它打印的 JSON。 */
export function runCoreJson<T = any>(args: string[], timeoutMs = 180_000): T {
  const r = runCore(args, timeoutMs)
  const text = r.stdout.trim()
  if (!text) {
    throw createError({
      statusCode: 500,
      statusMessage: `后端无输出：${r.stderr.slice(-300)}`,
    })
  }
  try {
    return JSON.parse(text) as T
  } catch {
    throw createError({
      statusCode: 500,
      statusMessage: `后端输出不是 JSON：${text.slice(0, 300)}`,
    })
  }
}
