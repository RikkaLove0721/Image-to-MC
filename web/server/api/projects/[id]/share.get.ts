import fs from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'
import { appDir, projectDir, readProject, checkModelName } from '../../../utils/store'
import { readViewerHtml } from '../../../utils/viewer'

/**
 * 导出"分享预览页"：把模型查看器做成**一个离线单文件 HTML**。
 *
 * 查看器本来就是单文件的（体素数据内联在 `<script id="model">` 里），
 * 唯一的缺口是 3D 引擎走 jsDelivr CDN —— 发给朋友、对方断网或者访问不了
 * CDN 就只剩白屏。所以这里把 three.js 与 OrbitControls 也塞进 HTML：
 *
 *   源码 → gzip → base64 → 塞进 `<script type="text/plain">`
 *   页面加载时用浏览器自带的 DecompressionStream 解压 → blob URL → import()
 *
 * 6 万方块的模型导出后约 530 KB，断网双击就能看，不需要装任何东西。
 * 兼容性：Chrome/Edge 80+、Safari 16.4+、Firefox 113+；更老的浏览器
 * 会显示模板里那句"引擎加载失败"的提示，不会白屏卡死。
 */

/** 引擎压缩结果的缓存：1.2 MB 的源码每次 gzip 要几十毫秒，没必要重复压。 */
let engineCache: { three: string; orbit: string } | null = null

/** 引擎源码放在 `public/vendor/engine/`：开发时在 web/public，打包后在 runtime/public。 */
async function readEngine(name: string): Promise<Buffer> {
  const candidates = [
    path.join(process.cwd(), 'public', 'vendor', 'engine', name),
    path.join(appDir(), 'web', 'public', 'vendor', 'engine', name),
    path.join(appDir(), 'runtime', 'public', 'vendor', 'engine', name),
  ]
  for (const p of candidates) {
    if (fs.existsSync(p)) return fs.readFileSync(p)
  }
  throw createError({ statusCode: 500, statusMessage: '缺少 3D 引擎资源，安装包不完整' })
}

async function engineBase64(): Promise<{ three: string; orbit: string }> {
  if (engineCache) return engineCache
  const [three, orbit] = await Promise.all([readEngine('three.module.js'), readEngine('OrbitControls.js')])
  engineCache = {
    three: zlib.gzipSync(three, { level: 9 }).toString('base64'),
    orbit: zlib.gzipSync(orbit, { level: 9 }).toString('base64'),
  }
  return engineCache
}

const IMPORTMAP_RE = /<script type="importmap">[\s\S]*?<\/script>\s*/
const MODULE_HEAD_RE =
  /<script type="module">\s*\nimport \* as THREE from 'three';\s*\nimport \{ OrbitControls \} from 'three\/addons\/controls\/OrbitControls\.js';\s*\n/

/** 把 CDN 版查看器改造成离线单文件。找不到锚点就报错，不返回半成品。 */
async function inlineEngine(html: string): Promise<string> {
  if (!IMPORTMAP_RE.test(html) || !MODULE_HEAD_RE.test(html)) {
    throw createError({ statusCode: 500, statusMessage: '查看器模板结构变了，导出失败（请更新软件）' })
  }
  const { three, orbit } = await engineBase64()

  const boot = `<script id="engine-three" type="text/plain">${three}</script>
<script id="engine-orbit" type="text/plain">${orbit}</script>
<script type="module">
/* 解压内联的引擎源码 → blob URL → 动态 import。
   这样单文件里没有一行外部请求，断网、离线、发微信都能打开。 */
async function inflate(id) {
  const b64 = document.getElementById(id).textContent.trim();
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return await new Response(stream).text();
}
let THREE, OrbitControls;
try {
  const threeUrl = URL.createObjectURL(new Blob([await inflate('engine-three')], { type: 'text/javascript' }));
  const ocSrc = (await inflate('engine-orbit')).replace(/from\\s+['"]three['"]/g, \`from '\${threeUrl}'\`);
  const ocUrl = URL.createObjectURL(new Blob([ocSrc], { type: 'text/javascript' }));
  THREE = await import(threeUrl);
  OrbitControls = (await import(ocUrl)).OrbitControls;
} catch (e) {
  document.getElementById('err').style.display = 'grid';
  throw e;
}
`

  return html.replace(IMPORTMAP_RE, '').replace(MODULE_HEAD_RE, boot)
}

export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id') || ''
  const project = readProject(id)
  const name = checkModelName(String(getQuery(event).name || ''))
  const file = path.join(projectDir(project.id), 'output', `${name}_view.html`)
  if (!fs.existsSync(file)) {
    throw createError({ statusCode: 404, statusMessage: '这个模型还没有预览页，重新生成一次即可' })
  }

  const html = await inlineEngine(readViewerHtml(file))
  const filename = `${name}-预览.html`
  setHeader(event, 'Content-Type', 'text/html; charset=utf-8')
  setHeader(event, 'Content-Disposition', `attachment; filename*=UTF-8''${encodeURIComponent(filename)}`)
  setHeader(event, 'Cache-Control', 'no-store')
  return html
})
