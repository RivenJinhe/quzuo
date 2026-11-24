import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../api'
import { citationSource } from '../format'
import type { Citation, GenerationResult, Page, Plan, PublicPlan, TaskType } from '../types'
import type { Notify } from '../ui/useToast'
import { diagnosticMessage } from './ui'
import type { OpenModal } from './ui'

export const taskLabels: Record<TaskType, string> = {
  review: '复习笔记',
  travel: '周末行程',
  activity: '活动流程',
}

export function timelineText(timeline: NonNullable<GenerationResult['timeline']>) {
  return [
    timeline.start && `出发 ${timeline.start}`,
    timeline.leave && `离开 ${timeline.leave}`,
    timeline.end && `到达 ${timeline.end}`,
  ]
    .filter(Boolean)
    .join(' → ')
}

export function resultToText(result: GenerationResult | null) {
  if (!result) return ''
  const lines = result.headline ? [`# ${result.headline}`] : []
  result.items?.forEach((item, index) => {
    lines.push('', `${index + 1}. ${item.title || ''}${item.minutes ? `（${item.minutes} 分钟）` : ''}`)
    if (item.detail) lines.push(`   ${item.detail}`)
  })
  if (result.timeline) lines.push('', `时间线：${timelineText(result.timeline)}`)
  return lines.join('\n')
}

function updatedAt(date: string) {
  return date.replace('T', ' ').slice(0, 19)
}

export function CitationSnapshot({ citation, link = false }: { citation: Citation; link?: boolean }) {
  return (
    <div className="item-row">
      <div className="t">{citation.material_title}</div>
      <div className="d">{citation.excerpt_snapshot}</div>
      <div className="card-meta">
        {citationSource(citation)}
        {citation.jump_url &&
          (link ? (
            <>
              {' '}
              ·{' '}
              <a href={citation.jump_url} target="_blank" rel="noopener">
                打开原链接
              </a>
            </>
          ) : (
            ' · 可跳转链接'
          ))}
      </div>
    </div>
  )
}

async function copyText(text: string) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Older browsers can still copy a selected textarea.
    }
  }
  const input = document.createElement('textarea')
  input.value = text
  input.readOnly = true
  input.style.cssText = 'position:fixed;top:-1000px;left:0;opacity:0'
  const container = document.querySelector('dialog[open]') ?? document.body
  container.append(input)
  try {
    input.select()
    input.setSelectionRange(0, text.length)
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    input.remove()
  }
}

function CopiedText({ text, copied }: { text: string; copied: boolean }) {
  const node = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const selection = window.getSelection()
    if (copied || !node.current || !selection) return
    const range = document.createRange()
    range.selectNodeContents(node.current)
    selection.removeAllRanges()
    selection.addRange(range)
  }, [copied, text])
  return (
    <>
      <div className="pre" id="copy-text" ref={node} style={{ maxHeight: 'none' }}>
        {text}
      </div>
      <div className="hint">{copied ? '已复制到剪贴板。' : '浏览器不让自动复制，请手动全选上面的文字。'}</div>
    </>
  )
}

export function usePlans(signal: AbortSignal, notify: Notify, openModal: OpenModal) {
  const [plans, setPlans] = useState<Plan[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const latestList = useRef(0)

  async function refresh() {
    const request = ++latestList.current
    setLoadError('')
    setLoading(true)
    try {
      const data = await api<Page<Plan>>('GET', '/plans?limit=50', undefined, signal)
      if (request === latestList.current) setPlans(data.items)
    } catch (error) {
      if (!signal.aborted && request === latestList.current) setLoadError(diagnosticMessage(error))
    } finally {
      if (request === latestList.current) setLoading(false)
    }
  }

  async function copy(id: string) {
    try {
      const data = await api<PublicPlan>('POST', `/plans/${id}/copy`, undefined, signal)
      const text = (data.title ? `${data.title}\n\n` : '') + data.text
      if (signal.aborted) return
      const copied = await copyText(text)
      if (!signal.aborted)
        openModal('复制内容（公开版，已去掉私人备注与选择理由）', <CopiedText text={text} copied={copied} />)
    } catch (error) {
      if (!signal.aborted) notify(diagnosticMessage(error), true)
    }
  }

  async function open(id: string) {
    try {
      const [plan, citations] = await Promise.all([
        api<Plan>('GET', `/plans/${id}`, undefined, signal),
        api<{ items: Citation[] }>('GET', `/plans/${id}/citations`, undefined, signal).catch(
          (error: unknown) => ({ error: diagnosticMessage(error) }),
        ),
      ])
      if (signal.aborted) return
      openModal(plan.title, <PlanDetail plan={plan} citations={citations} copy={() => copy(id)} />)
    } catch (error) {
      if (!signal.aborted) notify(diagnosticMessage(error), true)
    }
  }

  async function remove(plan: Plan) {
    if (!window.confirm(`删除方案「${plan.title}」？`)) return
    try {
      await api('DELETE', `/plans/${plan.id}`, undefined, signal)
      notify('已删除')
      await refresh()
    } catch (error) {
      if (!signal.aborted) notify(diagnosticMessage(error), true)
    }
  }

  return { plans, loading, loadError, refresh, copy, open, remove }
}

type PlanDetailProps = {
  plan: Plan
  citations: { items: Citation[] } | { error: string }
  copy: () => Promise<void>
}

function PlanDetail({ plan, citations, copy }: PlanDetailProps) {
  const [copying, setCopying] = useState(false)
  async function copyPublicPlan() {
    setCopying(true)
    try {
      await copy()
    } finally {
      setCopying(false)
    }
  }

  return (
    <>
      <div className="hint">
        {taskLabels[plan.task_type]} · v{plan.version} · 更新于 {updatedAt(plan.updated_at)}
      </div>
      <div className="pre" style={{ maxHeight: 'none' }}>
        {plan.body}
      </div>
      {plan.body_private_note && (
        <div className="notice">私人备注（仅自己可见）：{plan.body_private_note}</div>
      )}
      {plan.rationale && <div className="notice">选择理由（仅自己可见）：{plan.rationale}</div>}
      <div className="small-title">出处</div>
      {'error' in citations ? (
        <div className="blocked">{citations.error}</div>
      ) : citations.items.length ? (
        citations.items.map((citation) => <CitationSnapshot key={citation.id} citation={citation} />)
      ) : (
        <div className="hint">无</div>
      )}
      <div className="row">
        <button className="primary" id="pd-copy" disabled={copying} onClick={() => void copyPublicPlan()}>
          复制公开版
        </button>
      </div>
    </>
  )
}

type PlansPanelProps = {
  plans: ReturnType<typeof usePlans>
  active: boolean
  readable: boolean
  writable: boolean
}

export function PlansPanel({ plans, active, readable, writable }: PlansPanelProps) {
  const [query, setQuery] = useState('')
  const [pending, setPending] = useState(false)
  const visiblePlans = plans.plans.filter((plan) => plan.title.includes(query.trim()))

  async function run(action: () => Promise<void>) {
    setPending(true)
    try {
      await action()
    } finally {
      setPending(false)
    }
  }

  return (
    <section className={`panel${active ? '' : ' hidden'}`} id="tab-plans" aria-label="我的方案">
      <div className="toolbar">
        <input
          id="plan-q"
          className="grow"
          placeholder="在本页方案里过滤标题（本地过滤）"
          aria-label="过滤方案标题"
          disabled={!readable}
          value={query}
          onChange={(event) => setQuery(event.currentTarget.value)}
        />
        <button className="ghost" id="plan-refresh" disabled={!readable} onClick={() => void plans.refresh()}>
          刷新
        </button>
      </div>
      <div id="plan-list" className="list" aria-busy={plans.loading}>
        {!readable && <div className="hint">登录后可查看方案。</div>}
        {plans.loadError && (
          <div className="blocked" role="alert">
            {plans.loadError}
          </div>
        )}
        {readable && !plans.loading && !plans.loadError && visiblePlans.length === 0 && (
          <div className="hint">还没有保存的方案。</div>
        )}
        {visiblePlans.map((plan) => (
          <div className="card" key={plan.id}>
            <div className="card-head">
              <div style={{ flex: 1 }}>
                <div className="t">
                  {plan.title}
                  <span className="badge">{taskLabels[plan.task_type]}</span>
                  <span className="badge">v{plan.version}</span>
                </div>
                <div className="card-meta">
                  更新于 {updatedAt(plan.updated_at)} · 出处 {plan.citation_ids.length} 条
                </div>
              </div>
              <div className="card-actions">
                <button
                  className="ghost small act-open"
                  disabled={pending}
                  onClick={() => void run(() => plans.open(plan.id))}
                >
                  打开
                </button>
                <button
                  className="ghost small act-copy"
                  disabled={pending}
                  onClick={() => void run(() => plans.copy(plan.id))}
                >
                  复制
                </button>
                <button
                  className="ghost small danger act-del"
                  disabled={!writable || pending}
                  onClick={() => void run(() => plans.remove(plan))}
                >
                  删除
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

type SavePlanFields = {
  conversation_id: string
  generation_id: string
  title: string
  body: string
  body_private_note: string | null
  rationale: string | null
  confirmed_conditions: Record<string, unknown>
}

export function savePlan(fields: SavePlanFields, signal: AbortSignal) {
  return api<Plan>('POST', '/plans', fields, signal)
}

type SavePlanFormProps = {
  fields: Pick<
    SavePlanFields,
    'conversation_id' | 'generation_id' | 'confirmed_conditions' | 'title' | 'body'
  >
  signal: AbortSignal
  notify: Notify
  onSaved: (plan: Plan) => void
}

export function SavePlanForm({ fields, signal, notify, onSaved }: SavePlanFormProps) {
  const [saving, setSaving] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const title = String(data.get('title') ?? '').trim()
    const note = String(data.get('note') ?? '').trim()
    const rationale = String(data.get('rationale') ?? '').trim()
    setSaving(true)
    try {
      const plan = await savePlan(
        {
          ...fields,
          title: title || '我的方案',
          body: String(data.get('body') ?? ''),
          body_private_note: note || null,
          rationale: rationale || null,
        },
        signal,
      )
      if (!signal.aborted) onSaved(plan)
    } catch (error) {
      if (!signal.aborted) {
        const message =
          error instanceof ApiError && error.code === 'STALE_GENERATION'
            ? '这条结果已过期：条件改过了，请用最新一次生成保存'
            : diagnosticMessage(error)
        notify(message, true)
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <form className="stack" onSubmit={submit} aria-busy={saving}>
      <label>
        标题
        <input id="sp-title" name="title" maxLength={200} defaultValue={fields.title} disabled={saving} />
      </label>
      <label>
        正文（可编辑）
        <textarea
          id="sp-body"
          name="body"
          rows={10}
          maxLength={20000}
          defaultValue={fields.body}
          disabled={saving}
        />
      </label>
      <label>
        私人备注（公开版不含）
        <input
          id="sp-note"
          name="note"
          maxLength={2000}
          placeholder="比如：这道题还是没懂"
          disabled={saving}
        />
      </label>
      <label>
        选择理由（公开版不含）
        <input id="sp-why" name="rationale" maxLength={2000} placeholder="为什么这么安排" disabled={saving} />
      </label>
      <div className="hint">同一对话重复保存会更新原来那条，不会多出一份。</div>
      <div className="row">
        <button className="primary" id="sp-save" disabled={saving}>
          保存
        </button>
      </div>
    </form>
  )
}
