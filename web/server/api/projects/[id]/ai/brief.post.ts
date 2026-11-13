import path from 'node:path'
import { projectDir, readProject } from '../../../../utils/store'
import { runCoreJson } from '../../../../utils/core'

export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id') || ''
  const project = readProject(id)
  const body = (await readBody(event)) || {}
  const front = project.images.front
  if (!front) {
    throw createError({ statusCode: 400, statusMessage: '先上传正视图' })
  }
  const photo = path.join(projectDir(id), 'images', front.file)
  const width = String(Math.min(96, Math.max(16, Number(body.width) || 40)))
  return runCoreJson<any>(['--brief', '-i', photo, '--width', width])
})
