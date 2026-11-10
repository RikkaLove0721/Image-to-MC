import { ensureVision, publicAiConfig, visionTarget } from '../../utils/ai'

/**
 * 检测（或重新检测）视觉能力。
 *
 * GET  —— 只读缓存，不花钱：界面一进来就能显示"能看/不能看"。
 * POST —— 强制重测：用户换了模型、或者怀疑结论过期时点一下。
 */
export default defineEventHandler(async (event) => {
  const cfg = publicAiConfig()
  if (event.method === 'GET') {
    return { ...cfg, probed: cfg.visionOk !== null }
  }

  const t = visionTarget()
  if (!t) {
    return {
      ...cfg,
      probed: false,
      error: '还没配置 AI 密钥',
    }
  }
  const probe = await ensureVision(true)
  return { ...publicAiConfig(), probed: true, probe }
})
