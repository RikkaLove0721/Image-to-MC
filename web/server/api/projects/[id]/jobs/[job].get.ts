export default defineEventHandler((event) => {
  const id = getRouterParam(event, 'id') || ''
  const jobId = getRouterParam(event, 'job') || ''
  const project = readProject(id)
  const job = getJob(jobId)
  if (!job || job.projectId !== project.id) {
    throw createError({ statusCode: 404, statusMessage: '任务不存在或已失效' })
  }
  return {
    id: job.id,
    status: job.status,
    code: job.code,
    modelName: job.modelName,
    startedAt: job.startedAt,
    finishedAt: job.finishedAt,
    log: job.log,
    command: job.command,
  }
})
