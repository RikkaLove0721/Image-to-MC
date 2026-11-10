import { readAiConfig } from '../../utils/ai'

/**
 * 列出这个端点真正提供的模型。
 *
 * 为什么要这个接口：模型名是最容易出错、又最难自查的一环。
 * 服务端会悄悄改名单（`deepseek-chat` 这种老别名可能还在，也可能已经换成
 * `deepseek-flash`），而"名字写错了"和"没联网"在界面上长得一模一样。
 * 直接把 `/models` 的返回摆给用户，填错的情况就没了。
 */
export default defineEventHandler(async () => {
  const cfg = readAiConfig()
  if (!cfg.apiKey) return { models: [], error: '还没配置密钥' }
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 15_000)
  try {
    const res = await fetch(cfg.baseUrl.replace(/\/+$/, '') + '/models', {
      headers: { Authorization: `Bearer ${cfg.apiKey}` },
      signal: ctrl.signal,
    })
    if (!res.ok) {
      return { models: [], error: `接口返回 ${res.status}` }
    }
    const data = (await res.json()) as any
    const models = (data?.data || [])
      .map((m: any) => String(m?.id || ''))
      .filter(Boolean)
      .sort()
    return { models, error: '' }
  } catch (e: any) {
    return {
      models: [],
      error: e?.name === 'AbortError' ? '接口超时' : `读取失败：${String(e?.message || e).slice(0, 80)}`,
    }
  } finally {
    clearTimeout(timer)
  }
})
