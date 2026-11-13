import fs from 'node:fs'

export default defineEventHandler((event) => {
  const id = getRouterParam(event, 'id') || ''
  const project = readProject(id)
  if (activeJob(project.id)) {
    throw createError({ statusCode: 409, statusMessage: '任务运行中，先等它跑完再删' })
  }
  fs.rmSync(projectDir(project.id), { recursive: true, force: true })
  return { ok: true }
})
