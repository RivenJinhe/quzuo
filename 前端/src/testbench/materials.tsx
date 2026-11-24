import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import { materialPage } from '../materials/materials'
import type { ExtractedMaterial, Material, Topic } from '../types'
import type { Notify } from '../ui/useToast'
import { diagnosticMessage } from './ui'

type MaterialDraft = {
  title: string
  topic: string
  excerpt: string
  source_kind: string
  source_file: string
  source_page_start: string
  source_page_end: string
  source_url: string
  note: string
}

const emptyDraft: MaterialDraft = {
  title: '',
  topic: '',
  excerpt: '',
  source_kind: 'courseware',
  source_file: '',
  source_page_start: '',
  source_page_end: '',
  source_url: '',
  note: '',
}
const uploadHint =
  '支持 txt / md / pdf / docx / pptx，≤20MB。选中后先转成 Markdown 再填进摘录，可再改；不解析版式、不做 OCR。'
const statuses: Record<string, [label: string, color: string]> = {
  citable: ['可引用', 'ok'],
  usable: ['可引用', 'ok'],
  link_only: ['仅链接待补', 'warn'],
  unusable: ['不可用', 'bad'],
  pending: ['待补', 'bad'],
}

export const demoMaterial: MaterialDraft = {
  ...emptyDraft,
  title: '高数课件 第3章 极限',
  topic: '高数',
  excerpt:
    '极限的四则运算法则：若 lim f = A，lim g = B，则 lim(f+g) = A+B，lim(f·g) = A·B。第 8 页例题：先判断极限存在，再计算极限值。',
  source_file: '高数课件.pdf',
  source_page_start: '8',
  source_page_end: '12',
  note: '老师强调会考',
}

function materialPayload(draft: MaterialDraft) {
  return {
    title: draft.title.trim(),
    topic: draft.topic.trim(),
    excerpt: draft.excerpt.trim(),
    source_kind: draft.source_kind,
    source_file: draft.source_file.trim() || null,
    source_page_start: draft.source_page_start ? Number(draft.source_page_start) : null,
    source_page_end: draft.source_page_end ? Number(draft.source_page_end) : null,
    source_url: draft.source_url.trim() || null,
    note: draft.note.trim() || null,
  }
}

function downloadMarkdown(text: string, name: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `${name}.md`
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function useMaterials(signal: AbortSignal, notify: Notify) {
  const [materials, setMaterials] = useState<Material[]>([])
  const [topics, setTopics] = useState<Topic[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [query, setQuery] = useState('')
  const [topic, setTopic] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const filters = useRef({ query: '', topic: '' })
  const latestList = useRef(0)
  const latestTopics = useRef(0)

  function changeQuery(value: string) {
    filters.current.query = value
    setQuery(value)
  }

  function changeTopic(value: string) {
    filters.current.topic = value
    setTopic(value)
    void refreshList()
  }

  async function refreshList() {
    const request = ++latestList.current
    setLoading(true)
    setLoadError('')
    try {
      const data = await materialPage(
        filters.current.query.trim(),
        filters.current.topic,
        null,
        undefined,
        signal,
      )
      if (request === latestList.current) setMaterials(data.items)
    } catch (error) {
      if (!signal.aborted && request === latestList.current) setLoadError(diagnosticMessage(error))
    } finally {
      if (request === latestList.current) setLoading(false)
    }
  }

  async function refreshTopics() {
    const request = ++latestTopics.current
    try {
      const data = await api<{ items: Topic[] }>('GET', '/materials/topics', undefined, signal)
      if (request !== latestTopics.current) return
      setTopics(data.items)
      if (filters.current.topic && !data.items.some((item) => item.topic === filters.current.topic)) {
        changeTopic('')
      }
    } catch (error) {
      if (!signal.aborted && request === latestTopics.current) notify(diagnosticMessage(error), true)
    }
  }

  async function refresh() {
    await Promise.all([refreshList(), refreshTopics()])
  }

  function save(draft: MaterialDraft, editing?: Material | null) {
    const payload = materialPayload(draft)
    if (editing) {
      return api<Material>(
        'PATCH',
        `/materials/${editing.id}`,
        { ...payload, version: editing.version },
        signal,
      )
    }
    return api<Material>('POST', '/materials', payload, signal)
  }

  async function remove(material: Material) {
    await api<null>('DELETE', `/materials/${material.id}`, undefined, signal)
    setSelected((ids) => ids.filter((id) => id !== material.id))
    notify('已删除')
    await refresh()
  }

  return {
    materials,
    topics,
    selected,
    setSelected,
    query,
    topic,
    loading,
    loadError,
    changeQuery,
    changeTopic,
    refreshList,
    refresh,
    save,
    remove,
  }
}

type MaterialsPanelProps = {
  materials: ReturnType<typeof useMaterials>
  active: boolean
  readable: boolean
  writable: boolean
  signal: AbortSignal
  notify: Notify
  send: () => void
}

export function MaterialsPanel({
  materials,
  active,
  readable,
  writable,
  signal,
  notify,
  send,
}: MaterialsPanelProps) {
  const [draft, setDraft] = useState(emptyDraft)
  const [editing, setEditing] = useState<Material | null>(null)
  const [fileHint, setFileHint] = useState(uploadHint)
  const [markdown, setMarkdown] = useState<{ text: string; name: string } | null>(null)
  const [formRequest, setFormRequest] = useState<'save' | 'extract' | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const form = useRef<HTMLFormElement>(null)
  const topicInput = useRef<HTMLInputElement>(null)
  const topicValue = useRef('')
  const formLocked = !writable || formRequest !== null

  function field(name: keyof MaterialDraft, value: string) {
    if (name === 'topic') topicValue.current = value
    setDraft((current) => ({ ...current, [name]: value }))
  }

  function resetFile() {
    setMarkdown(null)
    setFileHint(uploadHint)
  }

  function reset() {
    setDraft(emptyDraft)
    topicValue.current = ''
    setEditing(null)
    resetFile()
  }

  function edit(material: Material) {
    setEditing(material)
    topicValue.current = material.topic
    setDraft({
      title: material.title,
      topic: material.topic,
      excerpt: material.excerpt,
      source_kind: material.source_kind,
      source_file: material.source_file ?? '',
      source_page_start: String(material.source_page_start ?? ''),
      source_page_end: String(material.source_page_end ?? ''),
      source_url: material.source_url ?? '',
      note: material.note ?? '',
    })
    resetFile()
    form.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (formLocked) return
    setFormRequest('save')
    try {
      await materials.save(draft, editing)
      notify(editing ? '已保存修改' : '材料已保存')
      reset()
      await materials.refresh()
    } catch (error) {
      if (!signal.aborted) notify(diagnosticMessage(error), true)
    } finally {
      setFormRequest(null)
    }
  }

  async function extract(file: File) {
    setFormRequest('extract')
    setMarkdown(null)
    setFileHint(`正在读取 ${file.name} …`)
    let focusTopic = false
    try {
      const body = new FormData()
      body.append('file', file)
      const data = await api<ExtractedMaterial>('POST', '/materials/extract', body, signal)
      setDraft((current) => ({
        ...current,
        title: data.suggested_title || current.title,
        source_kind: 'courseware',
        source_file: data.source_file || file.name,
        source_page_start: String(data.page_start ?? ''),
        source_page_end: String(data.page_end ?? ''),
        excerpt: data.excerpt,
      }))
      setMarkdown({ text: data.markdown || data.excerpt, name: data.suggested_title || file.name })
      const truncation = data.truncated ? '（正文过长，已截取前段，可自行删减）' : ''
      setFileHint(`已读取：${data.page_count} 页 / ${data.chars} 字${truncation}。检查正文，补上主题后保存。`)
      focusTopic = !topicValue.current.trim()
      notify('已把文件转成 Markdown，填进摘录')
    } catch (error) {
      if (!signal.aborted) {
        const message = diagnosticMessage(error)
        setFileHint(`读取失败：${message}`)
        notify(message, true)
      }
    } finally {
      setFormRequest(null)
      if (focusTopic) window.requestAnimationFrame(() => topicInput.current?.focus())
    }
  }

  async function remove(material: Material) {
    if (!window.confirm(`删除「${material.title}」？（软删除，出处快照仍可展开）`)) return
    setDeleting(material.id)
    try {
      await materials.remove(material)
      if (editing?.id === material.id) reset()
    } catch (error) {
      if (!signal.aborted) notify(diagnosticMessage(error), true)
    } finally {
      setDeleting(null)
    }
  }

  return (
    <section className={`panel${active ? '' : ' hidden'}`} id="tab-materials" aria-label="材料库">
      <div className="two-col">
        <div className="col-form">
          <h2 id="mat-form-title">{editing ? '编辑材料' : '新增材料'}</h2>
          <form id="mat-form" className="stack" ref={form} onSubmit={submit} aria-busy={formRequest !== null}>
            <label>
              标题
              <input
                name="title"
                required
                maxLength={120}
                placeholder="如：高数课件 第3章 极限"
                value={draft.title}
                disabled={formLocked}
                onChange={(event) => field('title', event.currentTarget.value)}
              />
            </label>
            <label>
              主题
              <input
                ref={topicInput}
                name="topic"
                required
                maxLength={40}
                placeholder="如：高数"
                value={draft.topic}
                disabled={formLocked}
                onChange={(event) => field('topic', event.currentTarget.value)}
              />
            </label>
            <div className="uploadbox">
              <label className="box-title" htmlFor="mat-file">
                从文件读取正文（可选）
              </label>
              <input
                type="file"
                id="mat-file"
                accept=".txt,.md,.markdown,.csv,.pdf,.docx,.pptx"
                disabled={formLocked}
                aria-describedby="mat-file-hint"
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0]
                  event.currentTarget.value = ''
                  if (file) void extract(file)
                }}
              />
              <div className="hint" id="mat-file-hint" role="status">
                {fileHint}
              </div>
              {markdown?.text && (
                <button
                  type="button"
                  className="ghost"
                  id="mat-dl"
                  onClick={() => downloadMarkdown(markdown.text, markdown.name)}
                >
                  下载 .md
                </button>
              )}
            </div>
            <label>
              摘录原文
              <textarea
                name="excerpt"
                required
                rows={5}
                placeholder="把课件原文/网页正文粘贴进来"
                value={draft.excerpt}
                disabled={formLocked}
                onChange={(event) => field('excerpt', event.currentTarget.value)}
              />
            </label>
            <label>
              来源类型
              <select
                name="source_kind"
                value={draft.source_kind}
                disabled={formLocked}
                onChange={(event) => field('source_kind', event.currentTarget.value)}
              >
                <option value="courseware">课件</option>
                <option value="manual">手工整理</option>
                <option value="web">网页链接</option>
              </select>
            </label>
            <div className="row">
              <label>
                文件名
                <input
                  name="source_file"
                  placeholder="高数课件.pdf"
                  value={draft.source_file}
                  disabled={formLocked}
                  onChange={(event) => field('source_file', event.currentTarget.value)}
                />
              </label>
              <label>
                起始页
                <input
                  name="source_page_start"
                  type="number"
                  min={1}
                  value={draft.source_page_start}
                  disabled={formLocked}
                  onChange={(event) => field('source_page_start', event.currentTarget.value)}
                />
              </label>
              <label>
                结束页
                <input
                  name="source_page_end"
                  type="number"
                  min={1}
                  value={draft.source_page_end}
                  disabled={formLocked}
                  onChange={(event) => field('source_page_end', event.currentTarget.value)}
                />
              </label>
            </div>
            <label>
              网页链接
              <input
                name="source_url"
                placeholder="https://…（有则可跳转）"
                value={draft.source_url}
                disabled={formLocked}
                onChange={(event) => field('source_url', event.currentTarget.value)}
              />
            </label>
            <label>
              备注（不参与公开版）
              <input
                name="note"
                maxLength={500}
                value={draft.note}
                disabled={formLocked}
                onChange={(event) => field('note', event.currentTarget.value)}
              />
            </label>
            <div className="row">
              <button
                type="submit"
                className="primary"
                id="mat-submit"
                disabled={formLocked || deleting === editing?.id}
              >
                {editing ? '保存修改' : '保存材料'}
              </button>
              <button
                type="button"
                className="ghost"
                id="mat-reset"
                disabled={Boolean(formRequest)}
                onClick={reset}
              >
                清空
              </button>
            </div>
          </form>
        </div>
        <div className="col-list">
          <form
            className="toolbar"
            onSubmit={(event) => {
              event.preventDefault()
              void materials.refreshList()
            }}
          >
            <input
              id="mat-q"
              placeholder="搜标题 / 摘录 / 备注"
              aria-label="搜标题、摘录或备注"
              className="grow"
              value={materials.query}
              disabled={!readable}
              onChange={(event) => materials.changeQuery(event.currentTarget.value)}
            />
            <select
              id="mat-topic"
              aria-label="材料主题"
              value={materials.topic}
              disabled={!readable}
              onChange={(event) => materials.changeTopic(event.currentTarget.value)}
            >
              <option value="">全部主题</option>
              {materials.topics.map(({ topic }) => (
                <option key={topic} value={topic}>
                  {topic}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="ghost"
              id="mat-refresh"
              disabled={!readable}
              onClick={() => void materials.refresh()}
            >
              刷新
            </button>
          </form>
          <div className="bulkbar">
            <span id="mat-sel-info">已选 {materials.selected.length} 条</span>
            <button className="primary small" id="mat-send" disabled={!writable} onClick={send}>
              送到生成 →
            </button>
          </div>
          <div id="mat-list" className="list" aria-busy={materials.loading}>
            {!readable && <div className="hint">登录后可查看材料。</div>}
            {materials.loadError && (
              <div className="blocked" role="alert">
                {materials.loadError}
              </div>
            )}
            {readable && !materials.loading && !materials.loadError && materials.materials.length === 0 && (
              <div className="hint">还没有材料。先在左边加一条。</div>
            )}
            {materials.materials.map((material) => {
              const selected = materials.selected.includes(material.id)
              const [status, color] = statuses[material.status] ?? [material.status, 'bad']
              const pages =
                material.source_page_start &&
                `第 ${material.source_page_start}${material.source_page_end && material.source_page_end !== material.source_page_start ? `-${material.source_page_end}` : ''} 页`
              const source = [
                material.source_kind === 'courseware' && '课件',
                material.source_file,
                pages,
                material.source_url && '链接',
                material.note && `备注：${material.note}`,
              ]
              return (
                <div className={`card${selected ? ' sel' : ''}`} key={material.id} data-id={material.id}>
                  <div className="card-head">
                    <input
                      type="checkbox"
                      className="pick"
                      aria-label={`选择${material.title}`}
                      checked={selected}
                      onChange={(event) => {
                        const checked = event.currentTarget.checked
                        materials.setSelected((ids) =>
                          checked ? [...ids, material.id] : ids.filter((id) => id !== material.id),
                        )
                      }}
                      style={{ width: 'auto', marginTop: 3 }}
                    />
                    <div style={{ flex: 1 }}>
                      <div className="t">
                        {material.title}
                        <span className={`badge ${color}`}>{status}</span>
                      </div>
                      <div className="card-meta">
                        {[material.topic, ...source].filter(Boolean).join(' · ')}
                      </div>
                      <div className="card-meta">
                        {material.excerpt.slice(0, 90)}
                        {material.excerpt.length > 90 && '…'}
                      </div>
                    </div>
                    <div className="card-actions">
                      <button
                        className="ghost small act-edit"
                        disabled={formLocked || deleting === material.id}
                        onClick={() => edit(material)}
                      >
                        编辑
                      </button>
                      <button
                        className="ghost small danger act-del"
                        disabled={
                          !writable ||
                          deleting !== null ||
                          (editing?.id === material.id && Boolean(formRequest))
                        }
                        onClick={() => void remove(material)}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </section>
  )
}
