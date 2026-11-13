import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { runCore } from './core'

/**
 * 查看器模板版本，必须和 `mc12/viewer.py` 的 `TEMPLATE_VERSION` 一致。
 *
 * `_view.html` 是构建时烘出来的，模板固定在文件里 —— 查看器改了东西
 * （加功能、删功能），老模型的页面还是旧壳。取文件时比对这行标记，
 * 版本旧的就让后端就地换壳：复用页面里已内联的体素数据重新套模板。
 */
export const VIEWER_TEMPLATE = '6'

export function viewerIsCurrent(html: string): boolean {
  return html.includes(`name="viewer-template" content="${VIEWER_TEMPLATE}"`)
}

/**
 * 读一份"当前模板"的查看器 HTML。
 *
 * 导出的分享页要发给别人，不能带旧壳；但也不能顺手改用户仓库里的文件
 * （用户可能就想留着旧的那份）。所以版本旧时复制一份去换壳，读完删掉。
 */
export function readViewerHtml(file: string): string {
  const html = fs.readFileSync(file, 'utf-8')
  if (viewerIsCurrent(html)) return html
  // 换壳时 `--upgrade-view` 会**用文件名当模型名**，所以临时文件必须保留
  // 原来的 `<模型名>_view.html`，只换个目录 —— 否则导出的分享页标题会变成
  // `_share`（第一版就是这么翻车的，页面上写着"_share"）。
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mc12-share-'))
  const tmp = path.join(dir, path.basename(file))
  try {
    fs.copyFileSync(file, tmp)
    runCore(['--upgrade-view', tmp], 60_000)
    return fs.readFileSync(tmp, 'utf-8')
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
}
