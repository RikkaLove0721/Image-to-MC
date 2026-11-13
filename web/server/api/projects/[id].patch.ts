export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id') || ''
  const body = await readBody(event)
  const project = readProject(id)
  const name = String(body?.name || '').trim()
  if (!name) {
    throw createError({ statusCode: 400, statusMessage: '项目名不能为空' })
  }
  project.name = name.slice(0, 40)
  return { project: saveProject(project) }
})
