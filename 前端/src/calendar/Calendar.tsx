import { useEffect, useId, useState } from 'react'
import { api, errorMessage } from '../api'
import { CalendarEventDialog, CalendarImportDialog } from './CalendarForms'
import type { CalendarEvent, CalendarImportResult } from './CalendarForms'
import './calendar.css'

const weekdays = ['一', '二', '三', '四', '五', '六', '日']
const noEvents: CalendarEvent[] = []

function localDate(year: number, month: number, day: number) {
  const value = new Date(0)
  value.setHours(12, 0, 0, 0)
  value.setFullYear(year, month, day)
  return value
}

function dateKey(value: Date) {
  const year = String(value.getFullYear()).padStart(4, '0')
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return [year, month, day].join('-')
}

export function calendarToday() {
  return dateKey(new Date())
}

function daysInMonth(month: string) {
  const [year, number] = month.split('-').map(Number)
  const offset = (localDate(year, number - 1, 1).getDay() + 6) % 7
  const days = localDate(year, number, 0).getDate()
  const cells = Math.ceil((offset + days) / 7) * 7
  return Array.from({ length: cells }, (_, index) => {
    const day = index - offset + 1
    return day < 1 || day > days ? null : month + '-' + String(day).padStart(2, '0')
  })
}

function moveMonth(month: string, step: number) {
  const [year, number] = month.split('-').map(Number)
  return dateKey(localDate(year, number - 1 + step, 1)).slice(0, 7)
}

function dateLabel(day: string) {
  const [year, month, number] = day.split('-').map(Number)
  return year + '年' + month + '月' + number + '日'
}

function dayEvents(events: CalendarEvent[], day: string) {
  return events.filter((event) => event.start_date <= day && event.end_date >= day)
}

function eventTime(event: CalendarEvent) {
  const start = event.start_time?.slice(0, 5)
  const end = event.end_time?.slice(0, 5)
  if (event.start_date !== event.end_date) {
    return [
      [dateLabel(event.start_date), start].filter(Boolean).join(' '),
      [dateLabel(event.end_date), end].filter(Boolean).join(' '),
    ].join(' 至 ')
  }
  return start ? [start, end].filter(Boolean).join('–') : '全天'
}

interface MonthResult {
  month: string
  revision: number
  events: CalendarEvent[]
  error?: string
}

type CalendarPanelProps = {
  day: string
  onDay: (day: string) => void
} & ({ home: true; openCalendar: () => void } | { home?: false })

export function CalendarPanel(props: CalendarPanelProps) {
  const { day: activeDay, onDay, home } = props
  const month = activeDay.slice(0, 7)
  const heading = useId()
  const today = calendarToday()
  const [load, setLoad] = useState<MonthResult | null>(null)
  const [revision, setRevision] = useState(0)
  const [dialog, setDialog] = useState<'import' | 'new' | CalendarEvent | null>(null)
  const [receipt, setReceipt] = useState<CalendarImportResult | null>(null)
  const current = load?.month === month && load.revision === revision ? load : null
  const events = current?.events ?? noEvents
  const dates = daysInMonth(month)
  const agenda = dayEvents(events, activeDay)
  const [year, number] = month.split('-').map(Number)

  useEffect(() => {
    const controller = new AbortController()
    api<CalendarEvent[]>('GET', '/calendar?month=' + month, undefined, controller.signal)
      .then((events) => {
        if (!controller.signal.aborted) setLoad({ month, revision, events })
      })
      .catch((error) => {
        if (!controller.signal.aborted) setLoad({ month, revision, events: noEvents, error: errorMessage(error) })
      })
    return () => controller.abort()
  }, [month, revision])

  function flip(step: number) {
    const next = moveMonth(month, step)
    onDay(today.startsWith(next + '-') ? today : next + '-01')
  }

  function finishImport(result: CalendarImportResult) {
    setDialog(null)
    setReceipt(result)
    if (result.dates.length) {
      const next = result.dates.find((day) => day.startsWith(month + '-')) ?? result.dates[0]
      onDay(next)
      setRevision((value) => value + 1)
    }
  }

  function finishEdit(day: string) {
    setDialog(null)
    onDay(day)
    setRevision((value) => value + 1)
  }

  const nav = (
    <div className="calendar-nav">
      <button type="button" className="btn quiet sm" onClick={() => onDay(today)}>今天</button>
      <button type="button" className="btn quiet sm calendar-arrow" aria-label="上个月"
        disabled={month === '0001-01'} onClick={() => flip(-1)}>‹</button>
      <button type="button" className="btn quiet sm calendar-arrow" aria-label="下个月"
        disabled={month === '9999-12'} onClick={() => flip(1)}>›</button>
    </div>
  )

  return (
    <section className={'pad calendar-panel' + (home ? ' calendar-home' : '')} aria-labelledby={heading}>
      <div className="calendar-panel-heading">
        {home ? (
          <h2 id={heading}>日历</h2>
        ) : (
          <div>
            <h1 className="title" id={heading}>日历</h1>
            <p className="sub">导入行程图片或文字，自动添加或更新日程。</p>
          </div>
        )}
        {props.home ? (
          <button type="button" className="btn" onClick={props.openCalendar}>打开日历 →</button>
        ) : (
          <button type="button" className="btn primary" onClick={() => setDialog('import')}>导入行程</button>
        )}
      </div>

      {receipt && (
        <div className="calendar-receipt" role="status">
          <p>导入完成 · 新增 {receipt.created} 条 · 更新 {receipt.updated} 条 · 已存在 {receipt.unchanged} 条</p>
          {receipt.warnings.length > 0 && (
            <ul>{receipt.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
          )}
        </div>
      )}

      <div className="calendar-layout">
        <div className="calendar-board">
          <div className="calendar-board-heading">
            <h3 aria-live="polite">{year} 年 {number} 月</h3>
            {nav}
          </div>
          <div className="calendar-weekdays" aria-hidden="true">
            {weekdays.map((day) => <span key={day}>{day}</span>)}
          </div>
          <div className="calendar-days" aria-busy={!current}>
            {dates.map((day, index) => {
              if (!day) return <div key={'empty-' + index} className="calendar-blank" aria-hidden="true" />
              const scheduled = dayEvents(events, day)
              return (
                <button
                  type="button"
                  className="calendar-day"
                  key={day}
                  aria-label={dateLabel(day) + (scheduled.length ? '，' + scheduled.length + ' 条日程' : '')}
                  aria-pressed={day === activeDay}
                  aria-current={day === today ? 'date' : undefined}
                  onClick={() => onDay(day)}
                >
                  <span className="calendar-number">{Number(day.slice(-2))}</span>
                  {scheduled.length > 0 && (
                    <>
                      <span className="calendar-marks" aria-hidden="true">
                        {scheduled.slice(0, 3).map((event) => <span key={event.id} />)}
                      </span>
                      <span className="calendar-chips" aria-hidden="true">
                        {scheduled.slice(0, 2).map((event) => (
                          <span key={event.id} title={event.title}>
                            {day === event.start_date && event.start_time && (
                              <time>{event.start_time.slice(0, 5)}</time>
                            )}
                            {event.title}
                          </span>
                        ))}
                        {scheduled.length > 2 && <small>另有 {scheduled.length - 2} 条</small>}
                      </span>
                    </>
                  )}
                </button>
              )
            })}
          </div>
        </div>
        <section className="calendar-agenda" aria-label="当日日程">
          <div className="calendar-agenda-heading">
            <h3>{dateLabel(activeDay)}</h3>
            {!home && <button type="button" className="btn sm" onClick={() => setDialog('new')}>新增日程</button>}
          </div>
          {!current && <p className="calendar-helper" role="status">正在读取日程…</p>}
          {current?.error && (
            <div>
              <p className="calendar-error" role="alert">{current.error}</p>
              <button type="button" className="btn sm" onClick={() => setRevision((value) => value + 1)}>重新加载</button>
            </div>
          )}
          {current && !current.error && !agenda.length && <p className="calendar-helper">这一天没有日程。</p>}
          <ul>
            {agenda.map((event) => (
              <li key={event.id}>
                <div className="calendar-event-text">
                  <span>{eventTime(event)}</span>
                  <strong>{event.title}</strong>
                  {event.location && <span>{event.location}</span>}
                  {!home && event.notes && <p>{event.notes}</p>}
                </div>
                {!home && (
                  <button type="button" className="btn quiet sm" aria-label={'编辑 ' + event.title}
                    onClick={() => setDialog(event)}>编辑</button>
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>

      {dialog === 'import' && <CalendarImportDialog today={today} close={() => setDialog(null)} complete={finishImport} />}
      {dialog && dialog !== 'import' && (
        <CalendarEventDialog event={dialog === 'new' ? null : dialog} day={activeDay}
          close={() => setDialog(null)} saved={finishEdit} />
      )}
    </section>
  )
}
