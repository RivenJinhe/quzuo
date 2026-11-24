import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage } from '../api'
import { formatRelativeTime, taskLabels } from '../format'
import type { Page, Plan } from '../types'
import { ActionMenu } from '../ui/ActionMenu'
import type { Notify } from '../ui/useToast'

/** The sidebar and the list share the same loaded plans. */
export function usePlans(enabled: boolean) {
  const [plans, setPlans] = useState<Plan[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [page, setPage] = useState({ offset: '0' })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const refresh = useCallback(() => setPage({ offset: '0' }), [])
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    setLoading(true)
    setError('')
    api<Page<Plan>>(
      'GET',
      '/plans?limit=50&offset=' + encodeURIComponent(page.offset),
      undefined,
      controller.signal,
    )
      .then((data) => {
        if (controller.signal.aborted) return
        setPlans((previous) =>
          page.offset === '0'
            ? data.items
            : [...new Map([...previous, ...data.items].map((p) => [p.id, p])).values()],
        )
        setNext(data.next_cursor)
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorMessage(e))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [enabled, page])
  return {
    plans,
    next,
    loading,
    error,
    refresh,
    more: () => next && setPage({ offset: next }),
    retry: () => setPage({ ...page }),
  }
}

interface ListProps {
  list: ReturnType<typeof usePlans>
  open: (id: string) => void
  copy: (id: string) => void
  newTask: () => void
  toast: Notify
}
export function Plans({ list, open, copy, newTask, toast }: ListProps) {
  async function remove(p: Plan) {
    if (!window.confirm('删除方案「' + p.title + '」？')) return
    try {
      await api('DELETE', '/plans/' + p.id)
      toast('已删除')
      list.refresh()
    } catch (error) {
      toast(errorMessage(error), true)
    }
  }
  return (
    <div className="pad">
      <div className="page-head">
        <div>
          <div className="eyebrow">Step 3 · 回看与复用</div>
          <h1 className="title">我的方案</h1>
          <p className="sub">
            复习笔记、行程、活动流程都存在这里。重复保存同一份会更新原记录，不会堆出一堆副本。
          </p>
        </div>
        <div className="row">
          <button className="btn" disabled={!list.plans.length} onClick={() => copy(list.plans[0].id)}>
            复制最近一份
          </button>
          <button className="btn primary" onClick={newTask}>
            ＋ 新安排
          </button>
        </div>
      </div>
      <div className="toolbar">
        <div className="chips">
          <span className="chip on">全部</span>
        </div>
        <div className="spacer" />
        <span className="muted-meta">
          按更新时间排序 · {list.next ? '已加载' : '共'} {list.plans.length} 份
        </span>
      </div>
      <div className="grid-plans">
        {list.plans.map((p) => (
          <article className="plan" key={p.id}>
            <div className="plan-top">
              <span className="badge topic">{taskLabels[p.task_type] ?? p.task_type}</span>
              <span className="mono muted-meta">{formatRelativeTime(p.updated_at)}</span>
            </div>
            <h4>
              <button className="plan-link" onClick={() => open(p.id)}>
                {p.title}
              </button>
            </h4>
            <p>
              {p.body.slice(0, 90)}
              {p.body.length > 90 ? '…' : ''}
            </p>
            <div className="plan-foot">
              <span>{p.citation_ids.length} 条出处</span>
              <span className="meta-dot" />
              <span>v{p.version}</span>
              <div className="plan-actions">
                <button className="btn quiet sm" onClick={() => copy(p.id)}>
                  复制
                </button>
                <ActionMenu
                  label={'操作方案「' + p.title + '」'}
                  className="btn quiet sm"
                  items={[
                    { label: '打开', onSelect: () => open(p.id) },
                    { label: '复制公开版', onSelect: () => copy(p.id) },
                    { label: '删除方案', onSelect: () => remove(p), danger: true },
                  ]}
                />
              </div>
            </div>
          </article>
        ))}
      </div>
      {list.loading && (
        <p role="status" className="material-feedback">
          正在读取方案…
        </p>
      )}
      {list.error && (
        <p className="notice bad" role="alert">
          {list.error}{' '}
          <button className="btn sm" onClick={list.retry}>
            重试
          </button>
        </p>
      )}
      {list.next && !list.error && !list.loading && (
        <button className="btn quiet sm" onClick={list.more}>
          加载更多
        </button>
      )}
      {!list.plans.length && !list.loading && !list.error && (
        <div className="empty visible-empty">
          <div className="empty-ic">案</div>
          <b>还没有方案</b>
          <p>在「做个安排」里生成一份，点「保存为方案」就会出现在这里。重复保存同一份会更新原记录。</p>
          <div>
            <button className="btn primary" onClick={newTask}>
              ＋ 去做第一个安排
            </button>
          </div>
        </div>
      )}
      <div className="blk plans-hint">
        <span>ⓘ</span> 重复保存同一份方案会更新原记录。生成中的方案也能再改条件，以最后一次输入为准。
      </div>
    </div>
  )
}
