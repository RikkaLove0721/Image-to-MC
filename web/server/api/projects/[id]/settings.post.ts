import { readProject, saveProject } from '../../../utils/store'

/**
 * 记住这个项目的生成设置。
 *
 * 比例尺 / 细节等级 / 建筑技巧性 / 补充要求都是"这个项目的口味"，
 * 不是全局偏好：同一个人可能一边做精细宫殿、一边做粗糙示意图，
 * 存全局就会出现"新项目继承了上一个项目的口味"。
 *
 * 这里只做范围夹取，不校验业务合法性 —— UI 传来的已经是 UI 允许的值，
 * 但文件是可以手改的，夹一下免得手改成 0 之后除零。
 */
export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id') || ''
  const project = readProject(id)
  const body = (await readBody(event)) || {}

  const num = (v: any, lo: number, hi: number, fallback: number) => {
    const n = Number(v)
    if (!Number.isFinite(n)) return fallback
    return Math.min(hi, Math.max(lo, n))
  }

  const prev = project.settings || {}
  project.settings = {
    // 0.5~8 格/米，和滑条一致
    scale: num(body.scale, 0.5, 8, prev.scale ?? 2),
    // 1~4 档
    detail: Math.round(num(body.detail, 1, 4, prev.detail ?? 2)),
    // 0 低 / 1 中 / 2 高
    craft: Math.round(num(body.craft, 0, 2, prev.craft ?? 1)),
    // 结构数量 1~5（体量增生等级）
    complexity: Math.round(num(body.complexity, 1, 5, prev.complexity ?? 2)),
    // 模型总宽（格）：0 = 自动
    modelWidth: Math.round(num(body.modelWidth, 0, 132, prev.modelWidth ?? 0)),
    hint: String(body.hint ?? prev.hint ?? '').slice(0, 400),
    // 分析宽度（AI 前端的采样宽度），16~96
    width: Math.round(num(body.width, 16, 96, prev.width ?? 40)),
  }
  saveProject(project)
  return { settings: project.settings }
})
