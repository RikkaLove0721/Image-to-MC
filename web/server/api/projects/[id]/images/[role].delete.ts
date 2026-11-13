import fs from 'node:fs'
import path from 'node:path'

export default defineEventHandler((event) => {
  const id = getRouterParam(event, 'id') || ''
  const role = getRouterParam(event, 'role') || ''
  const project = readProject(id)
  if (!VIEW_ROLES.includes(role as any)) {
    throw createError({ statusCode: 404, statusMessage: '没有这个视角' })
  }
  const image = project.images[role as 'front']
  if (image) {
    const dir = path.join(projectDir(project.id), 'images')
    for (const old of fs.readdirSync(dir)) {
      if (old.startsWith(`${role}.`)) fs.rmSync(path.join(dir, old), { force: true })
    }
    delete project.images[role as 'front']
    saveProject(project)
  }
  return { ok: true }
})
