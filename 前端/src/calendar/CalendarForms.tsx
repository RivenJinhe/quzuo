import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { api, errorMessage } from '../api'

export interface CalendarEventFields {
  title: string
  start_date: string
  end_date: string | null
  start_time: string | null
  end_time: string | null
  location: string | null
  notes: string | null
}

export interface CalendarEvent extends CalendarEventFields {
  id: string
  end_date: string
  version: number
}

export interface CalendarImportResult {
  created: number
  updated: number
  unchanged: number
  dates: string[]
  warnings: string[]
}

function CalendarDialog({ title, busy, close, children }: {
  title: string
  busy: boolean
  close: () => void
  children: ReactNode
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const label = useId()
  useEffect(() => {
    const element = dialog.current!
    element.showModal()
    return () => element.close()
  }, [])

  return (
    <dialog
      ref={dialog}
      className="calendar-dialog"
      aria-labelledby={label}
      onCancel={(event) => {
        event.preventDefault()
        if (!busy) close()
      }}
    >
      <h2 id={label}>{title}</h2>
      {children}
    </dialog>
  )
}

export function CalendarImportDialog({ today, close, complete }: {
  today: string
  close: () => void
  complete: (result: CalendarImportResult) => void
}) {
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (request.current || (!file && !text.trim())) return
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError('')
    const body = new FormData()
    body.set('reference_date', today)
    body.set('text', text)
    if (file) body.set('file', file)
    try {
      const result = await api<CalendarImportResult>('POST', '/calendar/import', body, controller.signal)
      if (!controller.signal.aborted) complete(result)
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorMessage(failure))
    } finally {
      request.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return (
    <CalendarDialog title="导入行程" busy={busy} close={close}>
      <form onSubmit={submit}>
        <label>
          行程文字
          <textarea
            className="textarea"
            rows={5}
            maxLength={12000}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={today + ' 14:00 小组讨论'}
            disabled={busy}
          />
        </label>
        <label>
          上传图片或文字文件
          <input
            className="calendar-file"
            type="file"
            accept=".png,.jpg,.jpeg,.webp,.txt,.md"
            disabled={busy}
            onChange={(event) => setFile(event.target.files?.item(0) ?? null)}
          />
        </label>
        <p className="calendar-helper">
          支持 PNG、JPG、WebP、TXT 和 Markdown，图片最大 8 MB。图片和需要模型分析的文字会发送给配置的模型服务。
        </p>
        {error && <p role="alert" className="calendar-error">{error}</p>}
        <div className="calendar-dialog-actions">
          <button type="button" className="btn" disabled={busy} onClick={close}>关闭</button>
          <button type="submit" className="btn primary" disabled={busy || (!file && !text.trim())}>
            {busy ? '正在识别…' : '识别并更新日历'}
          </button>
        </div>
      </form>
    </CalendarDialog>
  )
}

export function CalendarEventDialog({ event, day, close, saved }: {
  event: CalendarEvent | null
  day: string
  close: () => void
  saved: (day: string) => void
}) {
  const [fields, setFields] = useState<CalendarEventFields>(() => ({
    title: event?.title ?? '',
    start_date: event?.start_date ?? day,
    end_date: event && event.end_date !== event.start_date ? event.end_date : null,
    start_time: event?.start_time?.slice(0, 5) ?? null,
    end_time: event?.end_time?.slice(0, 5) ?? null,
    location: event?.location ?? null,
    notes: event?.notes ?? null,
  }))
  const [busy, setBusy] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])

  function field(key: keyof CalendarEventFields, value: string) {
    setFields((previous) => ({ ...previous, [key]: key === 'title' || key === 'start_date' ? value : value || null }))
  }

  async function write(remove = false) {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError('')
    try {
      const path = '/calendar/events'
      if (remove && event) {
        await api('DELETE', path + '/' + event.id + '?version=' + event.version, undefined, controller.signal)
        if (!controller.signal.aborted) saved(day)
      } else {
        const result = event
          ? await api<CalendarEvent>('PUT', path + '/' + event.id, { ...fields, version: event.version }, controller.signal)
          : await api<CalendarEvent>('POST', path, fields, controller.signal)
        if (!controller.signal.aborted) saved(result.start_date)
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorMessage(failure))
    } finally {
      request.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return (
    <CalendarDialog title={event ? '编辑日程' : '新增日程'} busy={busy} close={close}>
      <form onSubmit={(submit) => {
        submit.preventDefault()
        if (!removing) void write()
      }}>
        <label>
          日程名称
          <input className="input" value={fields.title} maxLength={160} required disabled={busy}
            onChange={(change) => field('title', change.target.value)} />
        </label>
        <div className="calendar-field-row">
          <label>
            开始日期
            <input className="input" type="date" value={fields.start_date} required disabled={busy}
              onChange={(change) => field('start_date', change.target.value)} />
          </label>
          <label>
            结束日期（可选）
            <input className="input" type="date" value={fields.end_date ?? ''} min={fields.start_date} disabled={busy}
              onChange={(change) => field('end_date', change.target.value)} />
          </label>
          <label>
            开始时间（可选）
            <input className="input" type="time" value={fields.start_time ?? ''} disabled={busy}
              onChange={(change) => field('start_time', change.target.value)} />
          </label>
          <label>
            结束时间（可选）
            <input className="input" type="time" value={fields.end_time ?? ''} disabled={busy}
              onChange={(change) => field('end_time', change.target.value)} />
          </label>
        </div>
        <label>
          地点
          <input className="input" value={fields.location ?? ''} maxLength={240} disabled={busy}
            onChange={(change) => field('location', change.target.value)} />
        </label>
        <label>
          备注
          <textarea className="textarea" value={fields.notes ?? ''} rows={2} maxLength={2000} disabled={busy}
            onChange={(change) => field('notes', change.target.value)} />
        </label>
        {removing && <p role="status" className="calendar-error">确定删除「{event?.title}」？</p>}
        {error && <p role="alert" className="calendar-error">{error}</p>}
        <div className="calendar-dialog-actions">
          {event && (
            <button type="button" className="btn calendar-remove" disabled={busy}
              onClick={removing ? () => void write(true) : () => setRemoving(true)}>
              {removing ? '确认删除' : '删除日程'}
            </button>
          )}
          <button type="button" className="btn" disabled={busy}
            onClick={removing ? () => setRemoving(false) : close}>
            {removing ? '取消删除' : '关闭'}
          </button>
          <button type="submit" className="btn primary" disabled={busy || removing || !fields.title.trim()}>
            {busy ? '正在提交…' : '保存日程'}
          </button>
        </div>
      </form>
    </CalendarDialog>
  )
}
