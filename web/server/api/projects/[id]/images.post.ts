import fs from 'node:fs'
import path from 'node:path'

const MIME_EXT: Record<string, string> = {
  'image/jpeg': 'jpg',
  'image/png': 'png',
  'image/webp': 'webp',
  'image/bmp': 'bmp',
}
const MAX_BYTES = 24 * 1024 * 1024

function sniffExt(data: Buffer): string | null {
  if (data.length > 3 && data[0] === 0xff && data[1] === 0xd8) return 'jpg'
  if (data.length > 8 && data[0] === 0x89 && data[1] === 0x50) return 'png'
  if (data.length > 12 && data.slice(0, 4).toString('ascii') === 'RIFF') return 'webp'
  if (data.length > 2 && data[0] === 0x42 && data[1] === 0x4d) return 'bmp'
  return null
}

export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id') || ''
  const project = readProject(id)
  const parts = await readMultipartFormData(event)
  if (!parts || !parts.length) {
    throw createError({ statusCode: 400, statusMessage: '没有收到文件' })
  }
  const rolePart = parts.find((p) => p.name === 'role' && !p.filename)
  const role = String(rolePart?.data?.toString('utf-8') || '').trim() as ViewRole
  if (!VIEW_ROLES.includes(role)) {
    throw createError({ statusCode: 400, statusMessage: '视角类型不对' })
  }
  const filePart = parts.find((p) => p.name === 'file' && p.filename)
  if (!filePart?.data?.length) {
    throw createError({ statusCode: 400, statusMessage: '文件内容为空' })
  }
  if (filePart.data.length > MAX_BYTES) {
    throw createError({ statusCode: 413, statusMessage: '单张图不要超过 24MB' })
  }
  const ext = sniffExt(filePart.data) || MIME_EXT[String(filePart.type || '')]
  if (!ext) {
    throw createError({ statusCode: 415, statusMessage: '只支持 JPG / PNG / WebP / BMP' })
  }

  const dir = path.join(projectDir(project.id), 'images')
  fs.mkdirSync(dir, { recursive: true })
  /* 同一个视角只留一张，换图时把旧的清掉，避免目录里堆一串历史文件 */
  for (const old of fs.readdirSync(dir)) {
    if (old.startsWith(`${role}.`)) fs.rmSync(path.join(dir, old), { force: true })
  }
  const file = `${role}.${ext}`
  fs.writeFileSync(path.join(dir, file), filePart.data)

  project.images[role] = {
    role,
    file,
    original: String(filePart.filename || file),
    bytes: filePart.data.length,
    uploadedAt: new Date().toISOString(),
  }
  saveProject(project)

  return {
    ok: true,
    role,
    url: `/api/projects/${project.id}/images/${role}?v=${Date.now()}`,
    project: projectDetail(project).project,
  }
})
