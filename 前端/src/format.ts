import type { TaskType } from './types'

export const taskLabels: Record<TaskType, string> = { review: '复习', travel: '出行', activity: '活动' }

export function formatRelativeTime(iso: string) {
  const elapsed = Math.floor((Date.now() - Date.parse(iso)) / 1000)
  if (elapsed < 60) return '刚刚'
  if (elapsed < 3600) return Math.floor(elapsed / 60) + ' 分钟前'
  if (elapsed < 86400) return Math.floor(elapsed / 3600) + ' 小时前'
  if (elapsed < 172800) return '昨天'
  if (elapsed < 2592000) return Math.floor(elapsed / 86400) + ' 天前'
  return iso.slice(0, 10)
}

export function citationSource(c: { source_file: string | null; source_page: string | number | null }) {
  return [c.source_file, c.source_page ? '第 ' + c.source_page + ' 页' : ''].filter(Boolean).join(' · ')
}
