/**
 * 小C 的台词库与"搭话"入口。
 *
 * 设定：活了千年的精灵。说话慢、句子短、不吵；心里其实很活络，
 * 偶尔会用完全平静的语气说一句调皮话。她喜欢这个用户 —— 但不黏人，
 * 也不吹捧，只在他卡住或者做完一件事的时候轻轻说一句。
 *
 * 用法：任何组件都能 `xiaocTell('build-done', { model: 'm03', elapsed: 12 })`，
 * 小C 自己决定说什么、要不要说（冷却与优先级在她那边控制）。
 */

export type XiaoCKind =
  | 'hello'
  | 'idle'
  | 'idle-night'
  | 'click'
  | 'drag'
  | 'build-start'
  | 'build-running'
  | 'build-done'
  | 'build-fail'
  | 'plan-start'
  | 'plan-done'
  | 'plan-fail'
  | 'viewer-open'
  | 'view-change'
  | 'model-select'
  | 'hide'
  | 'restore'

export interface XiaoCCtx {
  /** 产物名，例如 m03 */
  model?: string
  /** 这一轮花了多少秒 */
  elapsed?: number
  /** 体检分 */
  score?: number
  /** 视图名 */
  view?: string
}

type Line = (ctx: XiaoCCtx) => string
const fixed = (s: string): Line => () => s

export const XIAOC_LINES: Record<XiaoCKind, Line[]> = {
  hello: [fixed('你好，我是小C')],

  // 平时不说话，说一句就是观察结果。没有感叹号，也不催人。
  idle: [
    fixed('砖一块一块码，比日子好数。'),
    fixed('你按得很快，我不急。'),
    fixed('这一层的光是斜的，等一会儿会更好看。'),
    fixed('方块不会说谎，它只是不太会转弯。'),
    fixed('窗户的位置，是这栋楼的表情。'),
    fixed('我数完了这一边的柱子，二十七根。'),
    fixed('休息一下也没关系，我在这儿。'),
    fixed('你一皱眉，我就知道比例又不对了。'),
    fixed('我不太说话，不代表我没在看。'),
    fixed('这块石头有点旧了，但还站得住。'),
    fixed('慢一点。房子是给时间住的，不是给进度条住的。'),
    fixed('你已经在同一个角上改第四次了，我记住了。'),
  ],

  'idle-night': [
    fixed('这么晚了还在砌？我陪你。'),
    fixed('夜里安静，砖落下来的声音也清楚。'),
    fixed('这个点还醒着的人不多，你和我。'),
  ],

  click: [
    fixed('嗯，我在。'),
    fixed('有件事想让你知道——我很喜欢你捏的这些东西。'),
    fixed('被你点了一下，算今天的高光。'),
    fixed('你看，我一直在。'),
    fixed('要意见吗？我有一些。你可以不听。'),
    fixed('别急。做不完的东西，明天还是你的。'),
  ],

  drag: [
    fixed('搬我？我挺轻的。'),
    fixed('换个位置看模型，也好。'),
    fixed('这里的光比刚才那边好。'),
  ],

  'build-start': [
    fixed('开始砌了，我盯着。'),
    fixed('砖要落了。'),
    fixed('这个尺寸……有点意思。'),
    fixed('我不催你，机器也不催。'),
    fixed('先摆体块，再开窗。别急。'),
    fixed('这栋的角，得慢慢找。'),
    fixed('我把图纸看了一遍。开始吧。'),
    fixed('灰要起来了，往后退半步。'),
    fixed('一块一块来，急不出来的。'),
  ],

  'build-running': [
    fixed('还在砌。第二层了。'),
    fixed('别晃鼠标，我在对窗。'),
    fixed('这一段有点难，稍等。'),
    fixed('快了。屋角收完就好。'),
    fixed('方块数在涨。正常。'),
  ],

  'build-done': [
    (c) => `${c.model || '这一版'} 出来了。${c.elapsed ? `${c.elapsed} 秒，不算慢。` : '不算慢。'}`,
    fixed('好了。远看像，近看还得再改两处。'),
    (c) => `${c.model || '这一版'}比上一版稳。`,
    fixed('砌完了。要不要转一圈看看背面？'),
    fixed('完成。我把手放下来了。'),
  ],

  'build-fail': [
    fixed('塌了。不是你不行，是它没懂你的意思。'),
    fixed('这一版没成。日志里通常写着为什么。'),
    fixed('失败也是一种结果——至少知道哪条路不通。'),
    fixed('再试一次？我等着。'),
  ],

  'plan-start': [
    fixed('我先看一眼照片。'),
    fixed('让我数数它有几层。'),
    fixed('看图这件事，我做得比砌砖慢。'),
  ],

  'plan-done': [
    (c) => (c.score ? `方案有了，体检 ${c.score} 分。分只是参考，你的眼睛更准。` : '方案有了。'),
    fixed('我把它拆成了几块，你看看像不像。'),
    fixed('蓝图在这里。要不要先调一下比例尺？'),
  ],

  'plan-fail': [
    fixed('没看懂这张照片。换一张更正的视图试试。'),
    fixed('一轮都没走完。网络或者模型那边出了问题。'),
  ],

  'viewer-open': [
    fixed('转一圈吧，背面通常藏着问题。'),
    fixed('这个角度看，屋顶还算收得住。'),
    fixed('3D 里看得清——图里看不到的缝，都在这里。'),
    fixed('别只看正面。侧面最容易露馅。'),
  ],

  'view-change': [
    fixed('换了个角度。'),
    fixed('这样看，接缝明显了吗？'),
    fixed('剖开看，里面是空的——我们本来做的就只有壳。'),
  ],

  'model-select': [
    fixed('换一个看看。'),
    fixed('这一版和上一版，你想留哪个？'),
  ],

  hide: [fixed('那我先退到一边。')],
  restore: [fixed('又见面了。')],
}

/** 上一次抽到的下标，避免连着两次重复（按 kind 记） */
const lastIndex: Partial<Record<XiaoCKind, number>> = {}

/** 抽一句台词；同一个 kind 不会连着两次抽到同一句 */
export function xiaocLine(kind: XiaoCKind, ctx: XiaoCCtx = {}): string {
  const pool = XIAOC_LINES[kind]?.length ? XIAOC_LINES[kind] : XIAOC_LINES.idle
  let i = Math.floor(Math.random() * pool.length)
  if (pool.length > 1 && i === lastIndex[kind]) i = (i + 1) % pool.length
  lastIndex[kind] = i
  return pool[i]!(ctx)
}

/**
 * 让别处通知小C 说话。用 window 上的自定义事件，避免组件之间互相 import。
 * 服务端渲染时 window 不存在，直接跳过。
 */
export function xiaocTell(kind: XiaoCKind, ctx: XiaoCCtx = {}): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent('xiaoc:tell', { detail: { kind, ctx } }))
}
