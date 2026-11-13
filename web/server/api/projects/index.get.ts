export default defineEventHandler(() => {
  return { projects: listProjects(), root: projectsDir() }
})
