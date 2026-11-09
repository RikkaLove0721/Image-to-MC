export function formatDate(iso?: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '-'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export function formatDateTime(iso?: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '-'
  const p = (n: number) => String(n).padStart(2, '0')
  const offset = -d.getTimezoneOffset()
  const sign = offset >= 0 ? '+' : '-'
  const abs = Math.abs(offset)
  return `${formatDate(iso)} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}${sign}${p(
    Math.floor(abs / 60),
  )}:${p(abs % 60)}`
}

export function formatClock(value?: Date | string | null): string {
  if (!value) return '-'
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return '-'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function formatNumber(n?: number | null): string {
  if (n === undefined || n === null) return '-'
  return Number(n).toLocaleString('zh-CN')
}

export function formatBytes(n?: number | null): string {
  if (!n) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export const MODE_LABEL: Record<string, string> = {
  synth: '立面合成',
  building: '按照片重建',
  hall: '室内大厅',
  relief: '浮雕',
  flat: '平板',
  terrain: '地貌',
}

export function modeLabel(mode?: string): string {
  return MODE_LABEL[mode || ''] || mode || '未知'
}

export function sizeText(size?: number[] | null): string {
  if (!size || size.length < 3) return '-'
  return `${size[0]} × ${size[1]} × ${size[2]}`
}

export function relativeTime(iso?: string | null): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} 小时前`
  const day = Math.floor(hour / 24)
  if (day < 30) return `${day} 天前`
  return formatDate(iso)
}
