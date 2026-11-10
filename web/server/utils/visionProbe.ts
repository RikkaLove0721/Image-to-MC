import fs from 'node:fs'
import path from 'node:path'
import { appDir } from './store'

/**
 * 视觉能力探测。
 *
 * 为什么需要它：能力是**按模型**分的，不是按厂商分的。同一个端点下
 * `deepseek-flash` 能看图，`deepseek-v4-pro` 却把图片渲染成 `[Unsupported Image]`；
 * 而模型名又会随版本悄悄升级（`deepseek-chat` 一年前确实是纯文本）。
 * 所以"这个模型能不能看图"不能靠文档或记忆，只能当场问一句。
 *
 * 判据也不能是"接口没报错"——纯文本模型收到 image_url 往往不报错，
 * 只是把图默默丢掉，然后自信地编。真正的判据是**它能不能读出图里唯一
 * 确定的事实**：这张小图上是黑底白字 `K7Q`，看不见的模型答不出来。
 */

/** 200×80、1 位灰度、361 字节：白底黑字 `K7Q`。足够小，可以塞进源码。 */
const PROBE_PNG =
  'iVBORw0KGgoAAAANSUhEUgAAAMgAAABQAQAAAAB0FgcdAAABMElEQVR42u2UMW6EMBBFn8FKqMKW' +
  'qYKPsGW65Vjp4r2ZucEqJ0BKH1GksCLLP8VuAQaatNlpH5/5f2bAiJ2quJP/SQa3QFJs5V+hE7Vm' +
  'hZRa8QanBAWJbSZCN8FpRiqwZAO4tYNEqoDx0oZxSSzRAo7jcSo1U/OosQ99E5euUxs6KcgrPS4d' +
  'JIIDOEMq+/gehtWopPiEpFpGuS7yZAOgClPmSRXIAuQZsWCj2dlCAsjNBrFoX+NJh00Nu5oQiG5L' +
  'Y9x+n3HjXiqwD4eJqTegXGhuUfJaw21jRSwptqlWkMgv5axvz/j8ZUtv1bWz/17PQAD9QFNqrltw' +
  'H/5Q3o54D9IIbXk7+DDAM8RVn54TWPhZ73QcoC6yzt586czy+5nVeZd8Lr3Nap7H3P/xd/JH8gsc' +
  'PhJQhUayRAAAAABJRU5ErkJggg=='

const ANSWER = 'K7Q'

/** 探测结果缓存多久。模型会在服务端悄悄换版本，所以过一阵要重测。 */
const TTL_MS = 7 * 24 * 3600 * 1000

export interface ProbeResult {
  /** 真的读出了图里的字 */
  ok: boolean
  /** 被探测的模型名 */
  model: string
  /** 探测时刻（毫秒） */
  at: number
  /** 一句话说明，直接给界面显示 */
  note: string
}

function cachePath(): string {
  return path.join(appDir(), 'vision-probe.json')
}

function cacheKey(baseUrl: string, model: string): string {
  return `${baseUrl.replace(/\/+$/, '').toLowerCase()}::${model.toLowerCase()}`
}

export function readProbeCache(): Record<string, ProbeResult> {
  try {
    const raw = JSON.parse(fs.readFileSync(cachePath(), 'utf-8'))
    return raw && typeof raw === 'object' ? raw : {}
  } catch {
    return {}
  }
}

function writeProbeCache(all: Record<string, ProbeResult>) {
  try {
    fs.mkdirSync(path.dirname(cachePath()), { recursive: true })
    fs.writeFileSync(cachePath(), JSON.stringify(all, null, 2), 'utf-8')
  } catch {
    /* 缓存写不进去不影响功能，下次再探一遍而已 */
  }
}

/** 取缓存的探测结果；过期或换了模型就返回 null。 */
export function cachedProbe(baseUrl: string, model: string): ProbeResult | null {
  const hit = readProbeCache()[cacheKey(baseUrl, model)]
  if (!hit || typeof hit.ok !== 'boolean') return null
  if (Date.now() - Number(hit.at || 0) > TTL_MS) return null
  return hit
}

/** 真正发一次带图请求，看它能不能读出 K7Q。 */
export async function probeVision(
  baseUrl: string,
  model: string,
  apiKey: string,
  opts: { force?: boolean; timeoutMs?: number } = {},
): Promise<ProbeResult> {
  if (!opts.force) {
    const hit = cachedProbe(baseUrl, model)
    if (hit) return hit
  }

  const at = Date.now()
  const fail = (note: string): ProbeResult => ({ ok: false, model, at, note })

  if (!model || !apiKey) {
    return fail('没有可用的模型或密钥')
  }

  const url = baseUrl.replace(/\/+$/, '') + '/chat/completions'
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 45_000)
  let result: ProbeResult
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({
        model,
        messages: [
          {
            role: 'user',
            content: [
              { type: 'text', text: '图里是什么字符？只回答字符本身，不要解释。' },
              {
                type: 'image_url',
                image_url: { url: `data:image/png;base64,${PROBE_PNG}` },
              },
            ],
          },
        ],
        temperature: 0,
        max_tokens: 200,
        stream: false,
      }),
      signal: ctrl.signal,
    })
    if (!res.ok) {
      const text = await res.text()
      result = fail(`接口返回 ${res.status}：${text.slice(0, 120)}`)
    } else {
      const data = (await res.json()) as any
      const msg = data?.choices?.[0]?.message || {}
      // 推理模型可能把答案写在 reasoning_content 里，两处都要看
      const said = `${msg.content || ''} ${msg.reasoning_content || ''}`.toUpperCase()
      const ok = said.replace(/\s+/g, '').includes(ANSWER)
      result = ok
        ? { ok: true, model, at, note: `${model} 能读图` }
        : fail(`${model} 读不出探测图（无视觉能力）`)
    }
  } catch (e: any) {
    const aborted = e?.name === 'AbortError'
    result = fail(aborted ? `${model} 探测超时` : `${model} 探测失败：${String(e?.message || e).slice(0, 80)}`)
  } finally {
    clearTimeout(timer)
  }

  const all = readProbeCache()
  all[cacheKey(baseUrl, model)] = result
  writeProbeCache(all)
  return result
}
