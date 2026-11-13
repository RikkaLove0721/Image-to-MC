export default defineEventHandler((event) => {
  const id = getRouterParam(event, 'id') || ''
  const project = readProject(id)
  return {
    ...projectDetail(project),
    running: activeJob(project.id) || null,
  }
})
