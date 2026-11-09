/**
 * 桌面外壳桥接.
 *
 * 只有在「MC工程图.exe」里运行时, pywebview 才会往页面注入 window.pywebview,
 * 浏览器里打开时这些函数一律降级为普通网页行为.
 */

export interface AppInfo {
  desktop: boolean
  version: string
  projectsDir: string
  port: number
}

export interface SaveResult {
  ok: boolean
  path?: string
  canceled?: boolean
  error?: string
}

export interface DesktopBridge {
  app_info: () => Promise<AppInfo>
  save_file: (url: string, filename: string) => Promise<SaveResult>
  reveal_file: (path: string) => Promise<{ ok: boolean; error?: string }>
  open_viewer: (url: string, title: string) => Promise<{ ok: boolean; error?: string }>
}

export function desktopBridge(): DesktopBridge | null {
  if (typeof window === 'undefined') return null
  const host = (window as unknown as { pywebview?: { api?: unknown } }).pywebview
  if (!host || !host.api) return null
  return host.api as DesktopBridge
}

export function isDesktopApp(): boolean {
  return desktopBridge() !== null
}

/** 从接口地址取文件名: /api/projects/x/file/m01.schematic -> m01.schematic */
export function filenameOf(url: string): string {
  const clean = url.split('?')[0].split('#')[0]
  const tail = clean.slice(clean.lastIndexOf('/') + 1)
  return tail || 'download.bin'
}

export type SaveState = 'saved' | 'canceled' | 'failed' | 'browser'

/**
 * 保存产物. 桌面模式下走系统另存为对话框, 浏览器模式返回 browser 让调用方保持原生链接行为.
 */
export async function desktopSave(url: string, filename: string): Promise<SaveState> {
  const bridge = desktopBridge()
  if (!bridge) return 'browser'
  try {
    const res = await bridge.save_file(url, filename)
    if (res?.ok) return 'saved'
    if (res?.canceled) return 'canceled'
    return 'failed'
  } catch {
    return 'failed'
  }
}

export async function desktopOpenViewer(url: string, title: string): Promise<boolean> {
  const bridge = desktopBridge()
  if (!bridge) {
    window.open(url, '_blank')
    return true
  }
  try {
    const res = await bridge.open_viewer(url, title)
    return !!res?.ok
  } catch {
    return false
  }
}
