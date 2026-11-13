import { deleteModel, readProject } from '../../../../utils/store'

export default defineEventHandler((event) => {
  const id = getRouterParam(event, 'id') || ''
  const name = decodeURIComponent(getRouterParam(event, 'name') || '')
  const project = readProject(id)
  const result = deleteModel(project, name)
  return { ok: true, name, removed: result.removed }
})
