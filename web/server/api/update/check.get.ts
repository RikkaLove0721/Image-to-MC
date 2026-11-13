/** 查最新版本。`?force=1` 绕过 3 分钟缓存（用户手点"再检测一次"时用）。 */
export default defineEventHandler(async (event) => {
  const q = getQuery(event)
  const force = q.force === '1' || q.force === 'true'
  return await checkUpdate(force)
})
