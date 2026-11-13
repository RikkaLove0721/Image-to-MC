/**
 * 执行覆盖安装：写脚本 → 拉起脚本 → 返回。
 * 调用方拿到 ok 之后应马上让外壳退出（quit_app），脚本会等进程退出后覆盖并重启。
 */
export default defineEventHandler(async () => {
  return await installUpdate()
})
