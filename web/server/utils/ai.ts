import fs from 'node:fs'
import path from 'node:path'
import { appDir } from './store'
import { cachedProbe, probeVision, type ProbeResult } from './visionProbe'

/**
 * 视觉模型：只看图、只输出结构化判读，不写蓝图。
 *
 * 注意这是"可选覆盖"。留空就用主模型自己看图——现在 DeepSeek 的
 * `deepseek-chat` / `deepseek-flash` 都能接收 image_url，能力按模型分，
 * 不按厂商分（同端点的 `deepseek-v4-pro` 就看不见）。到底能不能看
 * 由 `visionProbe` 当场测，不靠配置、也不靠文档。
 */
export interface VisionConfig {
  baseUrl: string
  model: string
  apiKey: string
}

/** 本机 AI 配置：只存在用户数据目录，不进仓库、不进发布包。 */
export interface AiConfig {
  baseUrl: string
  model: string
  apiKey: string
  vision: VisionConfig
}

const DEFAULTS: AiConfig = {
  baseUrl: 'https://api.deepseek.com/v1',
  // 默认用 DeepSeek-V4.1-Flash。以前默认写的是 `deepseek-chat`，
  // 那是旧别名，界面上还一直显示它，用户会以为"接的是 chat 不是 flash"。
  // 服务端 /v1/models 现在给的是 deepseek-flash（名字叫 DeepSeek-V4.1-Flash，
  // 支持 text+image）和 deepseek-v4-pro（纯文本，读不了图）。
  model: 'deepseek-flash',
  apiKey: '',
  vision: { baseUrl: '', model: '', apiKey: '' },
}

/** 旧别名 → 现在的正式 id。读到就顺手迁掉，别让界面一直显示旧名字。 */
const MODEL_ALIASES: Record<string, string> = {
  'deepseek-chat': 'deepseek-flash',
  'deepseek-chat-latest': 'deepseek-flash',
  'deepseek-v4.1-flash': 'deepseek-flash',
  'deepseek-4.1-flash': 'deepseek-flash',
}

function normalizeModel(model: string): string {
  const m = String(model || '').trim()
  return MODEL_ALIASES[m] || m || DEFAULTS.model
}

function configPath(): string {
  return path.join(appDir(), 'ai.json')
}

function readVision(raw: any): VisionConfig {
  const v = raw?.vision || {}
  return {
    baseUrl: String(v.baseUrl || ''),
    model: String(v.model || ''),
    apiKey: String(v.apiKey || ''),
  }
}

export function readAiConfig(): AiConfig {
  try {
    const raw = JSON.parse(fs.readFileSync(configPath(), 'utf-8'))
    const model = normalizeModel(String(raw.model || DEFAULTS.model))
    // 读到旧别名（deepseek-chat）就写回正式 id，否则界面上永远显示旧的
    if (raw.model && raw.model !== model) {
      try {
        fs.writeFileSync(
          configPath(),
          JSON.stringify({ ...raw, model }, null, 2),
          'utf-8',
        )
      } catch {
        /* 写不进去也不影响本次使用 */
      }
    }
    return {
      baseUrl: String(raw.baseUrl || DEFAULTS.baseUrl),
      model,
      apiKey: String(raw.apiKey || ''),
      vision: readVision(raw),
    }
  } catch {
    return { ...DEFAULTS, vision: { ...DEFAULTS.vision } }
  }
}

/** 对外只暴露"有没有配好"，不回传密钥本身。 */
export function publicAiConfig() {
  const c = readAiConfig()
  const t = visionTarget()
  const probe = t ? cachedProbe(t.baseUrl, t.model) : null
  return {
    baseUrl: c.baseUrl,
    model: c.model,
    hasKey: c.apiKey.length > 0,
    keyHint: c.apiKey ? `${c.apiKey.slice(0, 4)}…${c.apiKey.slice(-4)}` : '',
    visionBaseUrl: c.vision.baseUrl,
    visionModel: c.vision.model,
    hasVisionKey: c.vision.apiKey.length > 0,
    visionKeyHint: c.vision.apiKey
      ? `${c.vision.apiKey.slice(0, 4)}…${c.vision.apiKey.slice(-4)}`
      : '',
    // 实际会用哪个模型看图：单独配了就用它，否则就是主模型
    visionEffective: t?.model || '',
    visionAuto: !!t && !t.custom,
    // 缓存里的探测结论（可能为 null：还没测过）
    visionOk: probe ? probe.ok : null,
    visionNote: probe ? probe.note : '',
  }
}

export function saveAiConfig(patch: any) {
  const cur = readAiConfig()
  const vis = patch?.vision || {}
  const next: AiConfig = {
    baseUrl: String(patch?.baseUrl ?? cur.baseUrl).trim() || DEFAULTS.baseUrl,
    model: normalizeModel(String(patch?.model ?? cur.model).trim()) || DEFAULTS.model,
    // 空字符串表示"不改"，避免前端把打码后的值写回去
    apiKey: patch?.apiKey ? String(patch.apiKey).trim() : cur.apiKey,
    vision: {
      // 地址 / 密钥留空 = 跟随主配置，用户通常只需要填一个模型名
      baseUrl: vis.baseUrl === undefined ? cur.vision.baseUrl : String(vis.baseUrl).trim(),
      // 模型名留空 = 关闭视觉。它是唯一能把视觉关掉的开关，所以不沿用旧值
      model: vis.model === undefined ? cur.vision.model : String(vis.model).trim(),
      apiKey: vis.apiKey ? String(vis.apiKey).trim() : cur.vision.apiKey,
    },
  }
  fs.mkdirSync(path.dirname(configPath()), { recursive: true })
  fs.writeFileSync(configPath(), JSON.stringify(next, null, 2), 'utf-8')
  return publicAiConfig()
}

/**
 * 视觉通道的目标：单独配了视觉模型就用它（地址/密钥可留空跟随主配置），
 * 没配就用主模型。返回 null 表示连主模型都没配好。
 */
export function visionTarget(): {
  baseUrl: string
  model: string
  apiKey: string
  /** 是否用了单独的视觉模型（false = 跟随主模型） */
  custom: boolean
} | null {
  const c = readAiConfig()
  const custom = !!c.vision.model
  const model = c.vision.model || c.model
  const apiKey = c.vision.apiKey || c.apiKey
  if (!model || !apiKey) return null
  return {
    baseUrl: (custom ? c.vision.baseUrl : '') || c.baseUrl,
    model,
    apiKey,
    custom,
  }
}

/** 有没有可用的视觉通道（只代表"配好了"，不代表"测过能看图"）。 */
export function visionReady(): boolean {
  return visionTarget() !== null
}

/**
 * 探测/复用视觉能力。force=true 强制重测（用户点了"重新检测"）。
 *
 * 结果带缓存：探测本身是一次真实请求，不该每次生成都付这个成本。
 * 但换模型、换端点、超过 7 天都会自动重测。
 */
export async function ensureVision(force = false): Promise<ProbeResult | null> {
  const t = visionTarget()
  if (!t) return null
  try {
    return await probeVision(t.baseUrl, t.model, t.apiKey, { force })
  } catch {
    return null
  }
}

export type ChatContent = string | any[]

/** OpenAI 兼容的 chat 接口。返回助手文本。 */
/** 一次对话花了多少 token。合计拿不到就按 0 算，不影响主流程。 */
export interface TokenUsage {
  prompt: number
  completion: number
  total: number
}

export function addUsage(a: TokenUsage | null, b: TokenUsage | null): TokenUsage {
  return {
    prompt: (a?.prompt || 0) + (b?.prompt || 0),
    completion: (a?.completion || 0) + (b?.completion || 0),
    total: (a?.total || 0) + (b?.total || 0),
  }
}

export async function chat(
  messages: { role: string; content: ChatContent }[],
  opts: {
    temperature?: number
    maxTokens?: number
    timeoutMs?: number
    vision?: boolean
    /** 拿到 usage 时回调一次，用来累计"这一轮花了多少 token" */
    onUsage?: (u: TokenUsage) => void
    /**
     * 推理强度。deepseek-flash 是推理模型，思维链**算在 max_tokens 里**：
     * 默认 high 时，一个 2000 token 的蓝图会先花掉大半预算在思考上，
     * JSON 还没写完就被截断 —— 表现出来就是"AI 返回的不是合法 JSON"。
     * 要结构化输出（蓝图/改蓝图）时传 low。
     */
    effort?: 'low' | 'high' | 'max'
  } = {},
): Promise<string> {
  const cfg = readAiConfig()
  // 视觉请求走"视觉通道"：没单独配置就回落到主模型自己看图
  const t = opts.vision ? visionTarget() : null
  const baseUrl = t ? t.baseUrl : cfg.baseUrl
  const model = t ? t.model : cfg.model
  const apiKey = t ? t.apiKey : cfg.apiKey

  if (!apiKey) {
    throw createError({
      statusCode: 400,
      statusMessage: '还没配置 AI 密钥：先到「AI 方案」里填接口地址、模型和密钥',
    })
  }

  // 已知看不了图就别再发：省一次往返，也给前端一句能读懂的话
  if (t) {
    const probe = cachedProbe(t.baseUrl, t.model)
    if (probe && !probe.ok) {
      throw createError({
        statusCode: 400,
        statusMessage: `模型 ${t.model} 看不了图（探测结果：${probe.note}）。换一个支持图片输入的模型，或给主模型换视觉版本。`,
      })
    }
  }

  const url = baseUrl.replace(/\/+$/, '') + '/chat/completions'
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 180_000)
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model,
        messages,
        temperature: opts.temperature ?? 0.4,
        max_tokens: opts.maxTokens ?? 2000,
        ...(opts.effort ? { reasoning_effort: opts.effort } : {}),
        stream: false,
      }),
      signal: ctrl.signal,
    })
    if (!res.ok) {
      const text = await res.text()
      // 服务端日志：上游报错信息常常很长，塞进 statusMessage 会被 Nitro 截断，
      // 排查时看不到真正的错因（比如"max_tokens 超了"或"模型不支持该参数"）。
      console.error(`[ai] ${model} ${res.status}: ${text.slice(0, 500)}`)
      throw createError({
        statusCode: 502,
        statusMessage: `AI 接口返回 ${res.status}：${text.slice(0, 200)}`,
      })
    }
    const data = (await res.json()) as any
    if (opts.onUsage && data?.usage) {
      const u = data.usage
      opts.onUsage({
        prompt: Number(u.prompt_tokens || 0),
        completion: Number(u.completion_tokens || 0),
        total: Number(u.total_tokens || 0) || (Number(u.prompt_tokens || 0) + Number(u.completion_tokens || 0)),
      })
    }
    const msg = data?.choices?.[0]?.message || {}
    const content = msg.content
    // 有些兼容实现把内容拆成数组（{type:'text',text:'…'}）
    const text = Array.isArray(content)
      ? content.map((p: any) => String(p?.text || '')).join('')
      : String(content ?? '')
    if (text.trim()) return text
    // 推理模型可能把答案写进了 reasoning_content（content 为空）：
    // 直接丢掉会让上游拿到空串去解析 JSON，报一个完全无关的错
    return String(msg.reasoning_content ?? '')
  } finally {
    clearTimeout(timer)
  }
}

/**
 * 把一个本地图片文件变成 OpenAI 兼容的 image_url 片段。
 *
 * 不在这里做缩放：缩放要解码图片，Node 侧没有图像库，硬塞一个 sharp
 * 会让发布包多几十兆。该缩的是 Python 那边（run.py --compare），
 * 它本来就有 Pillow，还顺便把"原图 + 模型"拼成一张，省一半 token。
 */
export function imagePart(filePath: string): Record<string, any> {
  const buf = fs.readFileSync(filePath)
  const ext = path.extname(filePath).toLowerCase()
  const mime = ext === '.png' ? 'image/png' : ext === '.webp' ? 'image/webp' : 'image/jpeg'
  return {
    type: 'image_url',
    image_url: { url: `data:${mime};base64,${buf.toString('base64')}` },
  }
}

/**
 * 把"写到一半被截断"的 JSON 补成合法的。
 *
 * 蓝图动辄 4~6 KB，模型一边想一边写，max_tokens 一满就停在半句上 ——
 * 表现出来就是"AI 返回的蓝图出错"。以前整段丢弃，用户只能重试，
 * 而重试十次可能九次都在同一处被截断。这里按括号栈补齐：
 * 已写完的体积块、窗、细节全部保住，只丢掉最后那个不完整的字段。
 */
function repairJson(text: string): string | null {
  const stack: string[] = []
  let inStr = false
  let esc = false
  let last = ''
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (inStr) {
      if (esc) esc = false
      else if (ch === '\\') esc = true
      else if (ch === '"') inStr = false
      continue
    }
    if (ch === '"') inStr = true
    else if (ch === '{' || ch === '[') stack.push(ch === '{' ? '}' : ']')
    else if (ch === '}' || ch === ']') stack.pop()
    if (!/\s/.test(ch)) last = ch
  }
  if (!stack.length && !inStr) return null
  let out = text
  if (inStr) out += '"'
  // 悬空的 "key": 或 "," 直接砍掉，不然补完括号还是解析不了
  if (/[:,]\s*$/.test(out)) out = out.replace(/[,]\s*$/, '')
  if (/:\s*$/.test(out)) out = out.replace(/:\s*$/, ': null')
  while (stack.length) out += stack.pop()
  return out
}

/** 从模型的回复里抠出 JSON；容忍 ```json 包裹、前后解释、尾逗号、被截断。 */
export function parseJsonLoose(raw: string): any | null {
  if (!raw) return null
  let text = raw.trim()
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fence) text = fence[1]
  const start = text.indexOf('{')
  if (start < 0) return null
  const body = text.slice(start)
  // 先试"最后一个 } 截断"（正常情况），再试补括号（max_tokens 用尽）
  for (const cand of [body, repairJson(body)]) {
    if (!cand) continue
    const end = cand.lastIndexOf('}')
    if (end <= 0) continue
    const slice = cand.slice(0, end + 1).replace(/,\s*([}\]])/g, '$1')
    try {
      return JSON.parse(slice)
    } catch {
      /* 下一个候选 */
    }
  }
  return null
}
