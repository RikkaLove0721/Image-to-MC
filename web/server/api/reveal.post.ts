import { spawn } from 'node:child_process'
import path from 'node:path'

/* 只允许打开项目根目录或某个项目自己的目录, 不接受任意路径 */
export default defineEventHandler(async (event) => {
  const body = (await readBody(event).catch(() => ({}))) || {}
  const base = projectsDir()
  let target = base
  if (body.id) {
    target = path.join(base, safeId(String(body.id)))
  }
  if (!target.startsWith(base)) {
    throw createError({ statusCode: 400, statusMessage: '路径不合法' })
  }
  const cmd = process.platform === 'win32' ? 'explorer.exe' : process.platform === 'darwin' ? 'open' : 'xdg-open'
  try {
    spawn(cmd, [target], { detached: true, stdio: 'ignore', windowsHide: false }).unref()
  } catch (err: any) {
    throw createError({ statusCode: 500, statusMessage: `打开失败: ${err?.message || err}` })
  }
  return { ok: true, dir: target }
})
