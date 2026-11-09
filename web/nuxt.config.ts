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
  experimental: {
    appManifest: false,
  },
})
