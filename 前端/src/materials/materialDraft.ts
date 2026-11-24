import type { Material } from '../types'
import { materialUrl, stripPageMarkers } from './materials'

const topicClues = [
  ['复习', /课件|课程|复习|讲义|课堂|课本|章节|练习|例题|考试|作业|教材|定理|lecture|homework|syllabus/gi],
  [
    '出行',
    /出行|行程|返校|交通|周末|展览|咖啡|餐厅|景点|地址|营业|人均|旅游|门店|店铺|restaurant|café|cafe|travel/gi,
  ],
  ['活动', /活动|迎新|社团|团建|招新|游戏|破冰|物料|流程|主持|分组|报名|参与者|icebreaker|workshop/gi],
] as const

export interface EditorFields {
  content: string
  sourceUrl: string
  sourceFile: string
  pageStart: string
  pageEnd: string
  title: string
  topic: string
  note: string
  webExcerpt: string
  webExcerptEdited: boolean
  fromFile: boolean
}

export interface WebExcerpt {
  final_url: string
  title: string
  text: string
  truncated: boolean
}

export function initialFields(material: Material | null): EditorFields {
  const web = material?.source_kind === 'web' && !!materialUrl(material.source_url)
  return {
    content: (web ? material?.source_url : material?.excerpt) ?? '',
    sourceUrl: web ? '' : (material?.source_url ?? ''),
    sourceFile: material?.source_file ?? '',
    pageStart: material?.source_page_start?.toString() ?? '',
    pageEnd: material?.source_page_end?.toString() ?? '',
    title: material?.title ?? '',
    topic: material?.topic ?? '',
    note: material?.note ?? '',
    webExcerpt: web ? (material?.excerpt ?? '') : '',
    webExcerptEdited: web && !!material?.excerpt,
    fromFile: material?.source_kind === 'courseware' && !!material.source_file,
  }
}

export function mainLink(fields: EditorFields): string {
  const input = fields.content.trim()
  return !fields.fromFile && /^(?:https?:\/\/|www\.)\S*$/i.test(input) ? input : ''
}

export function inferTopic(text: string, hasPages: boolean): string {
  let best = hasPages ? '复习' : '其他'
  let score = 0
  for (const [topic, pattern] of topicClues) {
    const hits = new Set((text.match(pattern) ?? []).map((match) => match.toLowerCase())).size
    if (hits > score) {
      best = topic
      score = hits
    }
  }
  return best
}

export function materialDraft(fields: EditorFields, original: Material | null, web: WebExcerpt | undefined) {
  const link = mainLink(fields)
  const rawUrl = link || fields.sourceUrl.trim()
  const sourceUrl = (link && materialUrl(web?.final_url)) || materialUrl(rawUrl)
  const excerpt = (link ? fields.webExcerpt : fields.content).trim()
  const sourceFile = fields.sourceFile.trim()
  const firstLine =
    stripPageMarkers(excerpt)
      .split('\n')
      .find((line) => line.trim()) ?? ''
  const inferredTitle =
    web?.title ||
    (link && sourceUrl ? new URL(sourceUrl).hostname : '') ||
    sourceFile.replace(/\.[^.]+$/, '') ||
    firstLine.replace(/^\s*(?:[#>*-]+\s+|\d+[.)]\s+)/, '').trim()
  const suggestedTitle = inferredTitle.slice(0, 120)
  const suggestedTopic = inferTopic(
    [inferredTitle, sourceFile, sourceUrl, excerpt.slice(0, 12000)].join(' '),
    /\.(?:pdf|pptx)$/i.test(sourceFile),
  )
  const statusUnchanged =
    original &&
    excerpt === original.excerpt &&
    sourceUrl === materialUrl(original.source_url) &&
    sourceFile === (original.source_file || '')
  const pageEnd = Number(fields.pageEnd) || null
  return {
    invalidUrl: !!rawUrl && !sourceUrl,
    suggestedTitle,
    suggestedTopic,
    body: {
      title: fields.title.trim() || suggestedTitle || '未命名材料',
      topic: fields.topic.trim() || suggestedTopic,
      excerpt,
      source_kind: sourceFile ? 'courseware' : sourceUrl ? 'web' : 'manual',
      source_url: sourceUrl || null,
      source_file: sourceFile || null,
      source_page_start: Number(fields.pageStart) || pageEnd,
      source_page_end: pageEnd,
      note: fields.note.trim() || null,
      status: statusUnchanged ? original.status : excerpt ? 'citable' : sourceUrl ? 'link_only' : 'unusable',
    },
  }
}
