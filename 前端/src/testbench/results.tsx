import { useRef, useState } from 'react'
import { api, pollGeneration } from '../api'
import type {
  Citation,
  Conversation,
  Generation,
  GenerationComparison,
  GenerationResult,
  GenerationSubmission,
  Plan,
} from '../types'
import type { Notify } from '../ui/useToast'
import { CitationSnapshot, resultToText, SavePlanForm, taskLabels, timelineText } from './plans'
import { diagnosticMessage } from './ui'
import type { OpenModal } from './ui'

const statuses: Record<string, [label: string, color: string]> = {
  pending: ['排队中', ''],
  running: ['生成中', ''],
  succeeded: ['已完成', 'ok'],
  blocked: ['被拦下', 'bad'],
  superseded: ['已作废', 'warn'],
  failed: ['失败', 'bad'],
  cancelled: ['已取消', 'warn'],
}

const standardFields = new Set([
  'headline',
  'items',
  'timeline',
  'budget',
  'pending_facts',
  'task_type',
  'materials_needed',
  'private_note',
  'rationale',
])

type ResultCard = {
  id: string
  model: string
  conversation: Conversation
  generation?: Generation
  cancelled?: boolean
  pollError?: string
}

function isPending(status: string) {
  return status === 'pending' || status === 'running'
}

function resultStatus(result: ResultCard) {
  return result.cancelled ? 'cancelled' : (result.generation?.status ?? 'pending')
}

export function useResults(signal: AbortSignal, notify: Notify) {
  const [results, setResults] = useState<ResultCard[]>([])
  const watches = useRef(new Map<string, AbortController>())

  function update(id: string, patch: Partial<ResultCard>) {
    setResults((rows) => rows.map((row) => (row.id === id ? { ...row, ...patch } : row)))
  }

  function clear() {
    watches.current.forEach((controller) => controller.abort())
    watches.current.clear()
    setResults([])
  }

  async function watch(id: string) {
    const controller = new AbortController()
    const abort = () => controller.abort()
    if (signal.aborted) return null
    signal.addEventListener('abort', abort, { once: true })
    watches.current.set(id, controller)
    try {
      return await pollGeneration(id, controller.signal, (generation) => update(id, { generation }))
    } catch (error) {
      if (!controller.signal.aborted) update(id, { pollError: diagnosticMessage(error) })
      return null
    } finally {
      watches.current.delete(id)
      signal.removeEventListener('abort', abort)
    }
  }

  function add(
    conversation: Conversation,
    submitted: GenerationSubmission,
    comparisons?: GenerationComparison[],
  ) {
    const comparison = comparisons ?? submitted.comparisons
    const cards: ResultCard[] = comparison
      ? comparison.map((entry) => ({
          id: entry.generation_id,
          model: entry.model_id,
          conversation,
        }))
      : submitted.id
        ? [{ id: submitted.id, model: submitted.model_id ?? 'model', conversation }]
        : []
    if (!cards.length) throw new Error('后端未返回生成 ID')
    const newestFirst = [...cards].reverse()
    setResults((rows) => [...newestFirst, ...rows])
    return cards.map((card) => watch(card.id))
  }

  async function cancel(id: string) {
    try {
      const data = await api<{ status: string }>('POST', `/generations/${id}/cancel`, undefined, signal)
      if (data.status === 'cancelled') {
        watches.current.get(id)?.abort()
        update(id, { cancelled: true, pollError: undefined })
        notify('已取消')
      } else {
        notify(`生成状态：${statuses[data.status]?.[0] ?? data.status}`)
      }
    } catch (error) {
      if (!signal.aborted) notify(diagnosticMessage(error), true)
    }
  }

  const active = results.filter((result) => !result.pollError && isPending(resultStatus(result))).length
  return { results, active, clear, add, cancel }
}

function displayDetail(detail: unknown) {
  if (detail === undefined || detail === null) return ''
  return typeof detail === 'string' ? detail : JSON.stringify(detail)
}

function Validation({ validation }: { validation: NonNullable<Generation['validation']> }) {
  const checks = validation.checks ?? []
  const failed = checks.filter((check) => check.ok === false && check.level !== 'notice').length
  const notices = checks.filter((check) => check.level === 'notice').length
  return (
    <>
      <div className="small-title">
        程序复核（{checks.length} 项 · 通过 {checks.length - failed - notices} · 提示 {notices} · 未通过{' '}
        {failed}）
      </div>
      <div className="checks">
        {checks.map((check, index) => (
          <div className="check-row" key={`${check.code}-${index}`}>
            <span className="c">
              {check.ok === false ? '✘' : check.level === 'notice' ? '⚠' : '✔'} {check.code}
            </span>
            <span>{displayDetail(check.detail).slice(0, 220)}</span>
          </div>
        ))}
      </div>
      {validation.notices?.map((notice, index) => (
        <div className="notice" key={`${notice.code}-${index}`}>
          提示：{notice.message || displayDetail(notice.detail) || notice.code}
        </div>
      ))}
      {validation.corrections?.map((correction, index) => (
        <div className="notice" key={`${correction.code}-${index}`}>
          已自动修正：{correction.code || JSON.stringify(correction)}
          {correction.material &&
            `（${correction.material}${correction.to !== undefined ? ` → ${displayDetail(correction.to)}` : ''}）`}
        </div>
      ))}
    </>
  )
}

type ResultContentProps = {
  generation: Generation
  showCitation: (citation: ResultCitation) => void
}
type ResultItem = NonNullable<GenerationResult['items']>[number]
type ResultCitation = NonNullable<ResultItem['citations']>[number]

function ResultContent({ generation, showCitation }: ResultContentProps) {
  if (generation.status === 'superseded') {
    return (
      <div className="notice">
        这条结果已作废：生成过程中改了条件或又发了一次，旧结果不再使用，也不会覆盖已保存的方案。
      </div>
    )
  }
  const result = generation.result
  const timeline = result?.timeline && timelineText(result.timeline)
  const extra =
    result && Object.fromEntries(Object.entries(result).filter(([field]) => !standardFields.has(field)))

  return (
    <>
      {generation.error_code && (
        <div className="blocked">
          错误 {generation.error_code}：{generation.error_message}
        </div>
      )}
      {generation.status === 'blocked' && generation.block_reason && (
        <div className="blocked">
          <b>被拦下：</b>
          {displayDetail(generation.block_reason)}
        </div>
      )}
      {result?.headline && <div style={{ fontWeight: 600 }}>{result.headline}</div>}
      {result?.items?.map((item, index) => (
        <div className="item-row" key={index}>
          <div className="t">
            {item.title}
            {item.must_keep && <span className="badge warn">必做</span>}
            {Boolean(item.minutes) && <span className="badge">{item.minutes} 分钟</span>}
          </div>
          <div className="d">{item.detail}</div>
          <div>
            {item.citations?.map((citation, citeIndex) => (
              <button
                type="button"
                className="cite"
                key={citeIndex}
                data-cid={citation.citation_id || citation.id}
                data-mat={citation.material_id}
                onClick={() => showCitation(citation)}
              >
                出处 {citeIndex + 1}
              </button>
            ))}
          </div>
        </div>
      ))}
      {timeline && <div className="oked">时间线：{timeline}</div>}
      {result?.budget && (
        <>
          <div className="small-title">费用</div>
          <div className="pre">{JSON.stringify(result.budget, null, 2)}</div>
        </>
      )}
      {Boolean(result?.pending_facts?.length) && (
        <div className="notice">待确认：{result?.pending_facts?.join('；')}</div>
      )}
      {generation.validation && <Validation validation={generation.validation} />}
      {extra && Object.keys(extra).length > 0 && (
        <details>
          <summary className="small-title">其它字段</summary>
          <div className="pre">{JSON.stringify(extra, null, 2)}</div>
        </details>
      )}
    </>
  )
}

type GenerationCardProps = {
  result: ResultCard
  writable: boolean
  cancel: (id: string) => Promise<void>
  signal: AbortSignal
  notify: Notify
  openModal: OpenModal
  onSaved: (plan: Plan) => void
}

export function GenerationCard({
  result,
  writable,
  cancel,
  signal,
  notify,
  openModal,
  onSaved,
}: GenerationCardProps) {
  const [cancelling, setCancelling] = useState(false)
  const { generation, id, conversation } = result
  const status = resultStatus(result)
  const [label, color] = statuses[status] ?? [status, '']

  async function cancelGeneration() {
    setCancelling(true)
    try {
      await cancel(id)
    } finally {
      setCancelling(false)
    }
  }

  async function showCitation(reference: ResultCitation) {
    try {
      const citationId = reference.citation_id || reference.id
      if (citationId) {
        const citation = await api<Citation>('GET', `/citations/${citationId}`, undefined, signal)
        if (!signal.aborted)
          openModal(`出处 · ${citation.material_title ?? ''}`, <CitationSnapshot citation={citation} link />)
        return
      }
      const data = await api<{ items: Citation[] }>('GET', `/generations/${id}/citations`, undefined, signal)
      const hits = data.items.filter((citation) => citation.material_id === reference.material_id)
      if (!signal.aborted)
        openModal(
          '出处',
          hits.length ? (
            hits.map((citation) => <CitationSnapshot key={citation.id} citation={citation} link />)
          ) : (
            <div className="hint">这条引用没有快照</div>
          ),
        )
    } catch (error) {
      if (!signal.aborted) notify(diagnosticMessage(error), true)
    }
  }

  function openSave() {
    if (!generation) return
    const fields = {
      conversation_id: conversation.id,
      generation_id: id,
      title: generation.result?.headline || taskLabels[conversation.task_type],
      body: resultToText(generation.result),
      confirmed_conditions: conversation.conditions,
    }
    openModal('保存方案', <SavePlanForm fields={fields} signal={signal} notify={notify} onSaved={onSaved} />)
  }

  return (
    <div className="gen-card" data-model={result.model} data-gid={id} data-status={status}>
      <div className="gen-head">
        <span className="m">{result.model}</span>
        <span className={`badge ${color}`}>{label}</span>
        <span className="spacer" />
        <button
          className="ghost small act-cancel"
          disabled={!writable || !isPending(status) || cancelling}
          onClick={() => void cancelGeneration()}
        >
          取消
        </button>
        <button
          className="ghost small act-save"
          disabled={!writable || status !== 'succeeded'}
          onClick={openSave}
        >
          保存方案
        </button>
      </div>
      <div className="gen-body">
        {result.pollError ? (
          <div className="blocked" role="alert">
            {result.pollError}（可在 Swagger 查询生成 {id}）
          </div>
        ) : (
          isPending(status) && (
            <div className="hint">{generation ? `模型正在生成…（${status}）` : '等待生成…'}</div>
          )
        )}
        {generation && !isPending(status) && (
          <ResultContent generation={generation} showCitation={(citation) => void showCitation(citation)} />
        )}
      </div>
    </div>
  )
}
