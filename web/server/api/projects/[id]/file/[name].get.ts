import fs from 'node:fs'
import path from 'node:path'
import { runCore } from '../../../../utils/core'

/**
 * 查看器模板版本，必须和 mc12/viewer.py 的 TEMPLATE_VERSION 一致。
 *
 * `_view.html` 是构建时烘出来的，模板固定在文件里 —— 查看器加了新功能
 * （比如方块查询），老模型的页面还是旧壳，用户会以为功能没做。
 * 所以取文件时比对一下标记，版本旧的就让后端**就地换壳**：
 * 复用页面里已内联的体素数据重新套模板，模型一格不动。
 */
const VIEWER_TEMPLATE = '5'

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
