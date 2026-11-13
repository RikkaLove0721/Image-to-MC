/** 放弃这次更新，清掉暂存目录。 */
export default defineEventHandler(async () => {
  return await cancelUpdate()
})
