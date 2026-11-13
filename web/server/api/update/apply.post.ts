/**
 * 下载并解压新版本到 `<安装目录>/update/staging`（不改动现有文件）。
 * 立即返回，进度靠 GET /api/update/state 轮询。
 */
export default defineEventHandler(async () => {
  return await prepareUpdate()
})
