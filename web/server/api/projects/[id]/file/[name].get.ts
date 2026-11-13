import fs from 'node:fs'
import path from 'node:path'
import { runCore } from '../../../../utils/core'
import { VIEWER_TEMPLATE } from '../../../../utils/viewer'

/* 模板版本与换壳逻辑统一放在 utils/viewer.ts：导出分享页那边也要用同一份版本号 */

const TYPES: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.html': 'text/html; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.schematic': 'application/octet-stream',
}

export default defineEventHandler((event) => {
  const id = getRouterParam(event, 'id') || ''
  const raw = getRouterParam(event, 'name') || ''
  const name = path.basename(decodeURIComponent(raw))
  const project = readProject(id)
  const output = path.join(projectDir(project.id), 'output')
  const file = path.join(output, name)
  /* 只允许取 output 目录下的文件，防止 ../ 穿越 */
  if (!file.startsWith(output + path.sep) || !fs.existsSync(file)) {
    throw createError({ statusCode: 404, statusMessage: '产物不存在' })
  }
  // 老查看器就地升级（只换 HTML 壳，不重建模型）
  if (name.endsWith('_view.html')) {
    try {
      const html = fs.readFileSync(file, 'utf-8')
      if (!html.includes(`name="viewer-template" content="${VIEWER_TEMPLATE}"`)) {
        runCore(['--upgrade-view', file], 60_000)
      }
    } catch {
      /* 升级失败就照旧吐旧页面，不影响下载 */
    }
  }

  const ext = path.extname(name).toLowerCase()
  setHeader(event, 'Content-Type', TYPES[ext] || 'application/octet-stream')
  setHeader(event, 'Cache-Control', 'public, max-age=30')
  if (getQuery(event).download) {
    setHeader(
      event,
      'Content-Disposition',
      `attachment; filename*=UTF-8''${encodeURIComponent(name)}`,
    )
  }
  return fs.readFileSync(file)
})
