import { saveAiConfig } from '../../utils/ai'

export default defineEventHandler(async (event) => {
  const body = (await readBody(event)) || {}
  return saveAiConfig({
    baseUrl: body.baseUrl,
    model: body.model,
    apiKey: body.apiKey,
    // 视觉模型是一组独立配置，可以只填模型名（地址与密钥跟随主配置）
    vision: body.vision,
  })
})
