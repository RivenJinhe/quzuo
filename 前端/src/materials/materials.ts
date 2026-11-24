import { api } from '../api'
import type { Material, Page } from '../types'

const PAGE_LIMIT = 50

export const MATERIAL_STATUS_LABEL: Readonly<Record<string, string>> = {
  citable: '能直接引用',
  usable: '能直接引用',
  link_only: '只能打开看',
  unusable: '读不出内容',
  pending: '待补',
}

export function materialPage(
  q: string,
  topic: string | null,
  cursor: string | null,
  limit = PAGE_LIMIT,
  signal?: AbortSignal,
): Promise<Page<Material>> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (q) params.set('q', q)
  if (topic) params.set('topic', topic)
  if (cursor) params.set('cursor', cursor)
  return api('GET', '/materials?' + params, undefined, signal)
}

export async function getSelectedMaterials(
  ids: readonly string[],
  signal?: AbortSignal,
): Promise<Material[]> {
  const requests: Promise<Page<Material>>[] = []
  for (let start = 0; start < ids.length; start += PAGE_LIMIT) {
    requests.push(api('POST', '/materials/batch', { ids: ids.slice(start, start + PAGE_LIMIT) }, signal))
  }
  return (await Promise.all(requests)).flatMap((page) => page.items)
}

export function materialUrl(text?: string | null): string {
  if (!text) return ''
  try {
    const url = new URL(/^www\./i.test(text) ? 'https://' + text : text)
    return /^https?:$/.test(url.protocol) && !url.username && !url.password && url.href.length <= 2000
      ? url.href
      : ''
  } catch {
    return ''
  }
}

export function stripPageMarkers(excerpt: string): string {
  return excerpt.replace(/<!-- page \d+ -->\s*/g, '')
}
