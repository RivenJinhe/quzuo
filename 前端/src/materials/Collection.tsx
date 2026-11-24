import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState } from 'react'
import type { SetStateAction } from 'react'
import { api, errorMessage } from '../api'
import { formatRelativeTime } from '../format'
import type { Material, Topic } from '../types'
import { ActionMenu } from '../ui/ActionMenu'
import { MaterialStatus } from './MaterialStatus'
import { getSelectedMaterials, materialPage, materialUrl, stripPageMarkers } from './materials'

const sourceKinds: Record<Material['source_kind'], string> = {
  web: '网页',
  courseware: '文件',
  manual: '手记',
}

interface MaterialsProps {
  active: boolean
  accountName: string
  selectedIds: string[]
  onSelectionChange: (update: SetStateAction<string[]>) => void
  onSendToArrange: (ids: string[]) => void | Promise<void>
  onEdit: (material: Material | null) => void
  onMaterialsDeleted: (ids: string[]) => void
  onMaterialsUpdated: (materials: Material[]) => void
  revision: number
  onCountChange: (count: number) => void
  onOpenAccount: () => void
  toast: (message: string, isError?: boolean) => void
}

interface Filters {
  query: string
  topic: string | null
  fetchAfter: number
}

interface CollectionQuery {
  filters: Filters
  revision: number
  refresh: number
}

interface CollectionPage {
  query: CollectionQuery
  items: Material[]
  cursor: string | null
  loading: boolean
  error: string
}

interface MaterialCardProps {
  material: Material
  checked: boolean
  disabled: boolean
  onToggle: (checked: boolean) => void
  onEdit: () => void
  onDelete: () => void
}

function MaterialCard({ material, checked, disabled, onToggle, onEdit, onDelete }: MaterialCardProps) {
  const url = materialUrl(material.source_url)
  const kind = sourceKinds[material.source_kind] || (material.source_file ? '文件' : '手记')
  return (
    <article
      className={checked ? 'mat picked' : 'mat'}
      data-id={material.id}
      onClick={(event) => {
        const clicked = event.target
        if (
          clicked instanceof Element &&
          event.currentTarget.contains(clicked) &&
          !clicked.closest('a, button, input')
        ) {
          onToggle(!checked)
        }
      }}
    >
      <div className="mat-top">
        <input
          type="checkbox"
          className="mat-check"
          aria-label={'选择 ' + material.title}
          checked={checked}
          onChange={(event) => onToggle(event.currentTarget.checked)}
        />
        <ActionMenu
          className="mat-more"
          label={'编辑或删除 ' + material.title}
          items={[
            { label: '编辑这份材料', onSelect: onEdit },
            { label: '删除这份材料', danger: true, onSelect: onDelete },
          ]}
          disabled={disabled}
        >
          ⋯
        </ActionMenu>
        <div>
          <div className="mat-title">{material.title}</div>
          {material.note && <div className="mat-note">你写过：{material.note}</div>}
          {material.excerpt && <div className="mat-excerpt">{stripPageMarkers(material.excerpt)}</div>}
          <div className="mat-status">
            <MaterialStatus status={material.status || 'citable'} />
          </div>
        </div>
      </div>
      <div className="mat-foot">
        <span className="badge topic">{material.topic || '其他'}</span>
        <span className="badge kind">{kind}</span>
        {material.source_file && <span>{material.source_file}</span>}
        {url && (
          <a href={url} target="_blank" rel="noopener noreferrer">
            打开原文
          </a>
        )}
        <span>{formatRelativeTime(material.updated_at || material.created_at)}</span>
      </div>
    </article>
  )
}

export function Materials({
  active,
  accountName,
  selectedIds,
  onSelectionChange,
  onSendToArrange,
  onEdit,
  onMaterialsDeleted,
  onMaterialsUpdated,
  revision,
  onCountChange,
  onOpenAccount,
  toast,
}: MaterialsProps) {
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState<Filters>({ query: '', topic: null, fetchAfter: 0 })
  const [refresh, setRefresh] = useState(0)
  const [topics, setTopics] = useState<Topic[]>([])
  const [page, setPage] = useState<CollectionPage | null>(null)
  const [busy, setBusy] = useState(false)
  const searchInput = useRef<HTMLInputElement>(null)
  const pageRequest = useRef<AbortController | null>(null)
  const actionRequest = useRef<AbortController | null>(null)
  const query = useMemo(() => ({ filters, revision, refresh }), [filters, revision, refresh])
  const current = page?.query === query ? page : null

  const onTopicsLoaded = useEffectEvent((next: Topic[]) => {
    setTopics(next)
    setFilters((old) =>
      old.topic && !next.some((item) => item.topic === old.topic)
        ? { ...old, topic: null, fetchAfter: 0 }
        : old,
    )
    onCountChange(next.reduce((sum, item) => sum + item.count, 0))
  })
  const onTopicsError = useEffectEvent((failure: unknown) => {
    toast('主题统计没加载出来：' + errorMessage(failure), true)
  })

  const loadPage = useCallback(
    async (
      requested: CollectionQuery,
      cursor: string | null,
      previous: Material[],
      controller: AbortController,
    ) => {
      const signal = controller.signal
      try {
        const data = await materialPage(
          requested.filters.query,
          requested.filters.topic,
          cursor,
          undefined,
          signal,
        )
        if (signal.aborted) return
        setPage({
          query: requested,
          items: previous.concat(data.items),
          cursor: data.next_cursor,
          loading: false,
          error: '',
        })
      } catch (failure) {
        if (signal.aborted) return
        setPage({
          query: requested,
          items: previous,
          cursor,
          loading: false,
          error: '没能加载收藏：' + errorMessage(failure),
        })
      } finally {
        if (pageRequest.current === controller) pageRequest.current = null
      }
    },
    [],
  )

  useEffect(() => {
    const controller = new AbortController()
    api<{ items: Topic[] }>('GET', '/materials/topics', undefined, controller.signal)
      .then(({ items }) => {
        if (!controller.signal.aborted) onTopicsLoaded(items)
      })
      .catch((failure) => {
        if (!controller.signal.aborted) onTopicsError(failure)
      })
    return () => controller.abort()
  }, [refresh, revision])

  useEffect(() => {
    const controller = new AbortController()
    pageRequest.current = controller
    const delay = query.filters.fetchAfter - performance.now()
    const start = () => void loadPage(query, null, [], controller)
    const timer = delay > 0 ? window.setTimeout(start, delay) : undefined
    if (timer === undefined) start()
    return () => {
      window.clearTimeout(timer)
      pageRequest.current?.abort()
      pageRequest.current = null
    }
  }, [query, loadPage])

  useEffect(() => () => actionRequest.current?.abort(), [])

  useEffect(() => {
    if (!active) return
    function focusSearch(event: KeyboardEvent) {
      if (event.key !== '/' || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey)
        return
      const target = event.target
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable || target.closest('input, textarea, select, dialog[open]'))
      )
        return
      if (document.querySelector('dialog[open]')) return
      event.preventDefault()
      searchInput.current?.focus()
    }
    document.addEventListener('keydown', focusSearch)
    return () => document.removeEventListener('keydown', focusSearch)
  }, [active])

  function nextPage() {
    if (!current || pageRequest.current) return
    const request = new AbortController()
    pageRequest.current = request
    setPage({ ...current, loading: true, error: '' })
    void loadPage(query, current.cursor, current.items, request)
  }

  function changeSearch(input: string) {
    setSearch(input)
    const value = input.trim()
    const fetchAfter = performance.now() + 240
    setFilters((old) => (old.query === value ? old : { ...old, query: value, fetchAfter }))
  }

  function changeTopic(topic: string | null) {
    setFilters((old) => (old.topic === topic ? old : { ...old, topic, fetchAfter: 0 }))
  }

  function toggleSelection(id: string, checked: boolean) {
    onSelectionChange((old) => {
      if (checked) return old.includes(id) ? old : [...old, id]
      return old.filter((picked) => picked !== id)
    })
  }

  async function runAction(operation: (signal: AbortSignal) => Promise<void>) {
    if (actionRequest.current) return
    const controller = new AbortController()
    actionRequest.current = controller
    setBusy(true)
    try {
      await operation(controller.signal)
    } catch (failure) {
      if (!controller.signal.aborted) toast(errorMessage(failure), true)
    } finally {
      actionRequest.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  function removeFromSelection(ids: string[]) {
    if (!ids.length) return
    const removed = new Set(ids)
    onSelectionChange((old) => old.filter((id) => !removed.has(id)))
    onMaterialsDeleted(ids)
  }

  function deleteOne(material: Material) {
    if (!window.confirm('删除「' + material.title + '」？')) return
    void runAction(async (signal) => {
      await api('DELETE', '/materials/' + material.id, undefined, signal)
      if (signal.aborted) return
      removeFromSelection([material.id])
      toast('已删除')
      setRefresh((n) => n + 1)
    })
  }

  function deleteMany() {
    if (!selectedIds.length || !window.confirm('删除选中的 ' + selectedIds.length + ' 份材料？')) return
    const ids = [...selectedIds]
    void runAction(async (signal) => {
      const removed: string[] = []
      let reason = ''
      for (const id of ids) {
        if (signal.aborted) return
        try {
          await api('DELETE', '/materials/' + id, undefined, signal)
          removed.push(id)
        } catch (error) {
          if (signal.aborted) return
          reason ||= errorMessage(error)
          console.error('删除材料失败', id, error)
        }
      }
      if (signal.aborted) return
      removeFromSelection(removed)
      toast(`已删除 ${removed.length} 份，共选中 ${ids.length} 份${reason && '。' + reason}`, !!reason)
      setRefresh((n) => n + 1)
    })
  }

  function changeTopics() {
    if (!selectedIds.length) return
    const topic = (window.prompt('改成哪个主题？', filters.topic || '复习') || '').trim().slice(0, 40)
    if (!topic) return
    const ids = [...selectedIds]
    void runAction(async (signal) => {
      const rows = await getSelectedMaterials(ids, signal)
      const updated: Material[] = []
      let reason = rows.length < ids.length ? '部分材料已不存在。' : ''
      for (const row of rows) {
        if (signal.aborted) return
        let revised: Material
        try {
          revised = await api<Material>(
            'PATCH',
            '/materials/' + row.id,
            { topic, version: row.version },
            signal,
          )
        } catch (error) {
          if (signal.aborted) return
          reason ||= errorMessage(error)
          console.error('更新材料主题失败', row.id, error)
          continue
        }
        if (signal.aborted) return
        updated.push(revised)
      }
      if (signal.aborted) return
      if (updated.length) onMaterialsUpdated(updated)
      toast(`已修改 ${updated.length} 份，共选中 ${ids.length} 份${reason && '。' + reason}`, !!reason)
      setRefresh((n) => n + 1)
    })
  }

  const materials = current?.items ?? []
  const loading = current?.loading ?? true
  const error = current?.error ?? ''
  const ready = !loading && !error
  const empty = ready && !materials.length
  const filtered = Boolean(filters.query || filters.topic)
  const count = selectedIds.length
  const selection = new Set(selectedIds)
  const allTopics = [{ topic: null, count: topics.reduce((sum, item) => sum + item.count, 0) }, ...topics]
  const addMaterial = () => onEdit(null)

  return (
    <div className="pad">
      <div className="page-head">
        <div>
          <div className="eyebrow">Step 1 · 先放材料</div>
          <h1 className="title">我的收藏</h1>
          <p className="sub">把课件、通知、手记收进来。做安排时只检索这里的材料，每句话都能点回出处。</p>
        </div>
        <div className="row">
          <button className="btn primary" type="button" disabled={busy} onClick={addMaterial}>
            ＋ 新增材料
          </button>
        </div>
        <button className="mob-account" type="button" onClick={onOpenAccount}>
          <span className="avatar" id="mobAvatar">
            {(accountName || '?').slice(0, 1).toUpperCase()}
          </span>
          <span id="mobName">{accountName || '未登录'}</span>
        </button>
      </div>

      <div className="toolbar">
        <label className="search">
          <span style={{ color: 'var(--faint)' }}>⌕</span>
          <input
            id="matSearch"
            ref={searchInput}
            type="search"
            maxLength={100}
            aria-label="搜标题、摘录或备注"
            placeholder="搜标题、摘录或备注…"
            value={search}
            onChange={(event) => changeSearch(event.currentTarget.value)}
          />
          <span className="kbd">/</span>
        </label>
        <div className="chips" id="topicChips">
          {allTopics.map((item) => (
            <button
              className={filters.topic === item.topic ? 'chip on' : 'chip'}
              aria-pressed={filters.topic === item.topic}
              type="button"
              key={item.topic ?? ''}
              onClick={() => changeTopic(item.topic)}
            >
              {item.topic || '全部'}
              <span className="n">{item.count}</span>
            </button>
          ))}
        </div>
        <div className="spacer" />
      </div>

      <div className="empty" id="emptyFirst" hidden={!empty || filtered}>
        <div className="empty-ic">收</div>
        <b>还没有材料</b>
        <p>先把课件、通知或手记收进来。做安排时只会检索这里的材料，每句话都能点回出处。</p>
        <div className="row" style={{ justifyContent: 'center' }}>
          <button className="btn primary" type="button" disabled={busy} onClick={addMaterial}>
            ＋ 新增材料
          </button>
        </div>
      </div>

      <div className="empty" id="emptyState" hidden={!empty || !filtered}>
        <div className="empty-ic">⌕</div>
        <b>没有匹配的材料</b>
        <p>换个词试试，或把主题切回「全部」。</p>
        <div className="row" style={{ justifyContent: 'center' }}>
          <button
            className="btn"
            type="button"
            onClick={() => {
              setSearch('')
              setFilters({ query: '', topic: null, fetchAfter: 0 })
            }}
          >
            清除筛选
          </button>
          <button className="btn primary" type="button" disabled={busy} onClick={addMaterial}>
            ＋ 新增材料
          </button>
        </div>
      </div>

      <div className="grid-materials" id="matGrid">
        {materials.map((material) => (
          <MaterialCard
            key={material.id}
            material={material}
            checked={selection.has(material.id)}
            disabled={busy}
            onToggle={(checked) => toggleSelection(material.id, checked)}
            onEdit={() => onEdit(material)}
            onDelete={() => deleteOne(material)}
          />
        ))}
      </div>

      <div className="row" style={{ justifyContent: 'center', marginTop: 14 }} id="gridFoot">
        <button
          className="btn quiet sm"
          id="moreMaterials"
          type="button"
          onClick={nextPage}
          hidden={!ready || !current?.cursor}
        >
          加载更多
        </button>
        <button className="btn quiet sm" id="retryMaterials" type="button" onClick={nextPage} hidden={!error}>
          重试
        </button>
        <span className="material-feedback" id="gridCount" role="status">
          {error ||
            (loading
              ? '正在加载收藏…'
              : materials.length
                ? '已显示 ' + materials.length + ' 份 · 按更新时间倒序'
                : '')}
        </span>
      </div>

      <div className="mob-add">
        <button className="btn primary" type="button" disabled={busy} onClick={addMaterial}>
          ＋ 新增材料
        </button>
      </div>

      <div className={count ? 'selectbar show' : 'selectbar'} id="selectbar" aria-hidden={!count}>
        <span>
          已选 <b id="selCount">{count}</b> 份材料
        </span>
        <span style={{ opacity: 0.45 }}>|</span>
        <span style={{ fontSize: 12, opacity: 0.7 }}>可送去本次安排</span>
        <button className="btn sm" type="button" disabled={!count || busy} onClick={changeTopics}>
          改主题
        </button>
        <button className="btn sm" type="button" disabled={!count || busy} onClick={deleteMany}>
          删除
        </button>
        <button className="btn sm" type="button" disabled={!count} onClick={() => onSelectionChange([])}>
          清空
        </button>
        <button
          className="btn primary sm"
          type="button"
          disabled={!count || busy}
          onClick={() => {
            void runAction(async () => onSendToArrange([...selectedIds]))
          }}
        >
          送入对话 →
        </button>
      </div>
    </div>
  )
}
