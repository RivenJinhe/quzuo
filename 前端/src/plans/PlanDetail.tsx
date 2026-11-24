import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api, errorMessage } from '../api'
import { citationSource, formatRelativeTime, taskLabels } from '../format'
import type { Citation, Plan } from '../types'
import type { Notify } from '../ui/useToast'

interface DetailProps {
  id: string
  back: () => void
  copy: (id: string) => void
  resume: (plan: Plan) => void
  showCitation: (id: string) => void
  onSaved: () => void
  toast: Notify
}

export function PlanDetail({ id, back, copy, resume, showCitation, onSaved, toast }: DetailProps) {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [citations, setCitations] = useState<Citation[] | null>(null)
  const [error, setError] = useState('')
  const [citationError, setCitationError] = useState('')
  const [editing, setEditing] = useState(false)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    const signal = controller.signal
    setError('')
    setCitationError('')
    api<Plan>('GET', '/plans/' + id, undefined, signal)
      .then((p) => {
        if (!signal.aborted) setPlan(p)
      })
      .catch((e) => {
        if (!signal.aborted) setError(errorMessage(e))
      })
    api<{ items: Citation[] }>('GET', '/plans/' + id + '/citations', undefined, signal)
      .then((data) => {
        if (!signal.aborted) setCitations(data.items)
      })
      .catch((e) => {
        if (!signal.aborted) setCitationError(errorMessage(e))
      })
    return () => controller.abort()
  }, [id, revision])

  if (editing && plan)
    return (
      <PlanEditor
        plan={plan}
        cancel={() => setEditing(false)}
        toast={toast}
        saved={(p) => {
          setPlan(p)
          setEditing(false)
          onSaved()
        }}
      />
    )
  return (
    <div className="pad">
      <button className="btn quiet sm" onClick={back}>
        ← 返回我的方案
      </button>
      {error ? (
        <p role="alert" className="notice bad">
          {error}
          <button className="btn sm" onClick={() => setRevision(revision + 1)}>
            重试
          </button>
        </p>
      ) : !plan ? (
        <p role="status">加载中…</p>
      ) : (
        <div id="detailGrid">
          <div>
            <div className="eyebrow">
              {taskLabels[plan.task_type] ?? plan.task_type} · v{plan.version} ·{' '}
              {formatRelativeTime(plan.updated_at)}
            </div>
            <h1 className="title detail-title">{plan.title}</h1>
            <div className="blk plan-body">{plan.body}</div>
            {plan.body_private_note && (
              <div className="blk plan-note">
                <b>私人备注 · 不会复制出去</b>
                {plan.body_private_note}
              </div>
            )}
            {plan.rationale && (
              <div className="blk plan-rationale">
                <b>选择理由 · 不会复制出去</b>
                {plan.rationale}
              </div>
            )}
            <div className="row detail-actions">
              <button className="btn primary" onClick={() => copy(id)}>
                复制公开版
              </button>
              <button className="btn" onClick={() => setEditing(true)}>
                编辑这份方案
              </button>
              <button className="btn" onClick={() => resume(plan)}>
                再改一次条件
              </button>
            </div>
          </div>
          <div>
            <div className="eyebrow">出处{citations && ' · ' + citations.length + ' 条'}</div>
            {citationError ? (
              <p role="alert">
                出处加载失败：{citationError}{' '}
                <button className="btn sm" onClick={() => setRevision(revision + 1)}>
                  重试
                </button>
              </p>
            ) : citations === null ? (
              <p role="status">正在读取出处…</p>
            ) : !citations.length ? (
              <p>这份方案没有保存出处快照。</p>
            ) : (
              citations.map((c, i) => (
                <div className="blk plan-citation" key={c.id}>
                  <b>
                    {i + 1}. {c.material_title}
                  </b>
                  <p>{c.excerpt_snapshot.slice(0, 220)}</p>
                  <div className="muted-meta">{citationSource(c) || '本账号材料'}</div>
                  <button className="btn quiet sm" onClick={() => showCitation(c.id)}>
                    查看出处
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function PlanEditor({
  plan,
  cancel,
  saved,
  toast,
}: {
  plan: Plan
  cancel: () => void
  saved: (plan: Plan) => void
  toast: Notify
}) {
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const fields = new FormData(event.currentTarget)
    const body = String(fields.get('body') || '')
    if (!body.trim()) {
      toast('正文不能空', true)
      return
    }
    setBusy(true)
    try {
      const updated = await api<Plan>('PATCH', '/plans/' + plan.id, {
        version: plan.version,
        title: String(fields.get('title') || '').trim() || plan.title,
        body,
        body_private_note: String(fields.get('note') || '').trim(),
        rationale: String(fields.get('rationale') || '').trim(),
      })
      saved(updated)
      toast('已保存（v' + updated.version + '）')
    } catch (e) {
      toast(errorMessage(e), true)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="pad">
      <button className="btn quiet sm" disabled={busy} onClick={cancel}>
        ← 取消编辑
      </button>
      <form className="plan-editor" onSubmit={submit}>
        <div className="eyebrow">编辑方案 · v{plan.version}</div>
        <label className="field">
          标题
          <input className="input" name="title" defaultValue={plan.title} disabled={busy} />
        </label>
        <label className="field">
          <span>
            正文 <span className="hint">复制公开版只用这一段</span>
          </span>
          <textarea className="input" name="body" rows={14} defaultValue={plan.body} disabled={busy} />
        </label>
        <label className="field">
          <span>
            私人备注 <span className="hint">只给你看，不会复制出去</span>
          </span>
          <textarea
            className="input"
            name="note"
            rows={3}
            defaultValue={plan.body_private_note ?? ''}
            disabled={busy}
          />
        </label>
        <label className="field">
          <span>
            选择理由 <span className="hint">只给你看，不会复制出去</span>
          </span>
          <textarea
            className="input"
            name="rationale"
            rows={3}
            defaultValue={plan.rationale ?? ''}
            disabled={busy}
          />
        </label>
        <div className="row">
          <button className="btn primary" disabled={busy}>
            {busy ? '保存中…' : '保存修改'}
          </button>
          <button className="btn" type="button" disabled={busy} onClick={cancel}>
            放弃修改
          </button>
        </div>
      </form>
    </div>
  )
}
