export default defineEventHandler(async (event) => {
  const body = await readBody(event)
  const name = String(body?.name || '').trim()
  if (!name) {
    throw createError({ statusCode: 400, statusMessage: '请先给项目起个名字' })
  }
  if (name.length > 40) {
    throw createError({ statusCode: 400, statusMessage: '项目名最多 40 个字' })
  }
  const project = createProject(name)
  return { project }
})
