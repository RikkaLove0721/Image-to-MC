import fs from 'node:fs'
import path from 'node:path'

const TYPES: Record<string, string> = {
  jpg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
  bmp: 'image/bmp',
}

export default defineEventHandler((event) => {
  const id = getRouterParam(event, 'id') || ''
  const role = getRouterParam(event, 'role') || ''
  const project = readProject(id)
  if (!VIEW_ROLES.includes(role as any)) {
    throw createError({ statusCode: 404, statusMessage: '没有这个视角' })
  }
  const image = project.images[role as 'front']
  if (!image) {
    throw createError({ statusCode: 404, statusMessage: '这张视角还没上传' })
  }
  const file = path.join(projectDir(project.id), 'images', image.file)
  if (!fs.existsSync(file)) {
    throw createError({ statusCode: 404, statusMessage: '图片文件不见了' })
  }
  const ext = path.extname(file).slice(1).toLowerCase()
  setHeader(event, 'Content-Type', TYPES[ext] || 'application/octet-stream')
  setHeader(event, 'Cache-Control', 'public, max-age=60')
  return fs.readFileSync(file)
})
