import fs from 'node:fs'
import path from 'node:path'

let version = '0.10.0'
try {
  version = fs.readFileSync(path.resolve(process.cwd(), '../VERSION'), 'utf-8').trim()
} catch {
  /* 版本文件不在也不影响启动 */
}

export default defineNuxtConfig({
  ssr: false,
  devtools: { enabled: false },
  css: ['~/assets/css/main.css'],
  app: {
    head: {
      htmlAttrs: { lang: 'zh-CN' },
      title: 'MC 工程图 · 照片转 Minecraft 建筑',
      meta: [
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
        { name: 'description', content: '把建筑照片转成 Minecraft 1.12.2 方块模型' },
      ],
    },
  },
  runtimeConfig: {
    public: { version },
  },
  nitro: {
    // 3D 引擎的源码，导出"分享预览页"时内联进 HTML。
    // 不走 public/ 是因为它是给服务端读的（不对外暴露成静态文件）。
    serverAssets: [{ baseName: 'engine', dir: './server/assets/engine' }],
  },
  experimental: {
    appManifest: false,
  },
})
