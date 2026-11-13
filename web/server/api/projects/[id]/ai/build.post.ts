import fs from 'node:fs'
import path from 'node:path'
import { projectDir, readProject, saveProject, nextModelName } from '../../../../utils/store'
import { startJob } from '../../../../utils/jobs'

export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id') || ''
  const project = readProject(id)
  const body = (await readBody(event)) || {}
  const spec = body.spec
  if (!spec || typeof spec !== 'object') {
    throw createError({ statusCode: 400, statusMessage: '缺少 spec' })
  }
  const dir = projectDir(id)
  const specPath = path.join(dir, 'spec.json')
  fs.writeFileSync(specPath, JSON.stringify(spec, null, 2), 'utf-8')

  const modelName = String(body.name || '') || nextModelName(project)
  const job = startJob(project, modelName, {
    spec: specPath,
    // token 花费 + 分析过程：构建完成后由 jobs.ts 回填进清单与 _ai.json
    meta: body.meta || null,
  })
  project.lastModel = modelName
  saveProject(project)
  return { job: { id: job.id, status: job.status, modelName: job.modelName } }
})
