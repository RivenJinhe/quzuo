import { useCallback, useEffect, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import { errorMessage } from '../api'
import { MaterialStatus } from '../materials/MaterialStatus'
import { MATERIAL_STATUS_LABEL, materialPage } from '../materials/materials'
import type { Material } from '../types'
import type { ArrangeMaterialChange, Selection } from './selection'

interface PickerPage {
  items: Material[]
  cursor: string | null
  loading: boolean
  error: string
}

const emptyPage: PickerPage = { items: [], cursor: null, loading: false, error: '' }

function CandidatePicker({
  selection,
  disabled,
  materialChange,
  onAdd,
}: {
  selection: Selection
  disabled: boolean
  materialChange?: ArrangeMaterialChange | null
  onAdd: (material: Material) => void
}) {
  const [opened, setOpened] = useState(false)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(emptyPage)
  const request = useRef<AbortController | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const query = search.trim()
  const candidates = new Set(selection.candidates.map((candidate) => candidate.material_id))

  const loadPage = useCallback(
    async (cursor: string | null, controller: AbortController) => {
      request.current = controller
      setPage((current) => ({ ...current, loading: true, error: '' }))
      try {
        const data = await materialPage(query, null, cursor, 8, controller.signal)
        if (controller.signal.aborted) return
        setPage((current) => ({
          items: cursor ? [...current.items, ...data.items] : data.items,
          cursor: data.next_cursor,
          loading: false,
          error: '',
        }))
      } catch (error) {
        if (controller.signal.aborted) return
        setPage((current) => ({ ...current, loading: false, error: `没能加载收藏：${errorMessage(error)}` }))
      }
    },
    [query],
  )

  useEffect(() => {
    if (!opened) return
    const controller = new AbortController()
    setPage({ ...emptyPage, loading: true })
    const timer = window.setTimeout(() => void loadPage(null, controller), query ? 240 : 0)
    return () => {
      window.clearTimeout(timer)
      request.current?.abort()
      controller.abort()
    }
  }, [loadPage, opened, query])

  useEffect(() => {
    if (!materialChange) return
    if (materialChange.kind === 'deleted') {
      const ids = new Set(materialChange.ids)
      setPage((current) => ({ ...current, items: current.items.filter((material) => !ids.has(material.id)) }))
    } else {
      const materials = materialChange.kind === 'saved' ? [materialChange.material] : materialChange.materials
      const byId = new Map(materials.map((material) => [material.id, material]))
      setPage((current) => ({
        ...current,
        items: current.items.map((material) => byId.get(material.id) ?? material),
      }))
    }
  }, [materialChange])

  function add(event: MouseEvent<HTMLButtonElement>, material: Material) {
    const button = event.currentTarget
    const items = button.closest('.candidate-items')
    const nextButton = Array.from(items?.querySelectorAll<HTMLButtonElement>('button:enabled') ?? []).find(
      (candidate) => candidate !== button,
    )
    onAdd(material)
    ;(nextButton ?? input.current)?.focus()
  }

  return (
    <details
      className="candidate-picker more"
      onToggle={(event) => {
        if (event.currentTarget.open) setOpened(true)
      }}
    >
      <summary>从收藏中添加材料</summary>
      <input
        className="input"
        type="search"
        maxLength={100}
        ref={input}
        value={search}
        aria-label="搜所有收藏的标题、摘录或备注"
        placeholder="搜标题、摘录或备注…"
        disabled={disabled}
        onChange={(event) => setSearch(event.target.value)}
      />
      <div className="candidate-items">
        {page.items.map((material) => {
          const added = candidates.has(material.id)
          return (
            <div className="cand" key={material.id}>
              <div style={{ minWidth: 0 }}>
                <div className="t">{material.title}</div>
                <div className="r">
                  {material.topic} · {MATERIAL_STATUS_LABEL[material.status] ?? material.status}
                </div>
              </div>
              <button
                className="btn quiet sm"
                type="button"
                aria-label={`加入 ${material.title}`}
                disabled={disabled || added}
                onClick={(event) => add(event, material)}
              >
                {added ? '已加入' : '加入'}
              </button>
            </div>
          )
        })}
      </div>
      <div className="candidate-help" role="status">
        {page.error ||
          (page.loading ? '正在搜索收藏…' : opened && !page.items.length ? '没有匹配的材料。' : '')}
      </div>
      {(page.cursor || page.error) && !page.loading && (
        <button
          className="btn quiet sm candidate-more"
          type="button"
          disabled={disabled}
          onClick={() => void loadPage(page.cursor, new AbortController())}
        >
          {page.error ? '重试' : '加载更多'}
        </button>
      )}
    </details>
  )
}

export function Candidates({
  selection,
  busy,
  confirming,
  materialChange,
  onToggle,
  onRemove,
  onEdit,
  onAdd,
  onConfirm,
  onReopen,
}: {
  selection: Selection
  busy: boolean
  confirming: boolean
  materialChange?: ArrangeMaterialChange | null
  onToggle: (id: string, checked: boolean) => void
  onRemove: (id: string) => void
  onEdit: (id: string) => void
  onAdd: (material: Material) => void
  onConfirm: () => void
  onReopen: () => void
}) {
  const body = useRef<HTMLDivElement>(null)
  const chosen = new Set(selection.ids)

  function remove(event: MouseEvent<HTMLButtonElement>, id: string) {
    const nextButton = Array.from(
      body.current?.querySelectorAll<HTMLButtonElement>('.remove-candidate:enabled') ?? [],
    ).find((button) => button !== event.currentTarget)
    onRemove(id)
    ;(nextButton ?? body.current?.querySelector('summary'))?.focus()
  }

  return (
    <>
      <h5>确认本次使用的材料</h5>
      <div className="candidate-body" ref={body} hidden={selection.confirmed}>
        <div className="candidate-rows">
          {selection.candidates.map((candidate) => (
            <div className="cand" key={candidate.material_id}>
              <input
                className="cand-check"
                type="checkbox"
                aria-label={`使用 ${candidate.title}`}
                checked={chosen.has(candidate.material_id)}
                disabled={busy || !candidate.usable}
                onChange={(event) => onToggle(candidate.material_id, event.target.checked)}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="t">{candidate.title}</div>
                {candidate.reason && <div className="why">{candidate.reason}</div>}
                {!candidate.usable && (
                  <div className="r">
                    <MaterialStatus status={candidate.status || 'unusable'} />
                    {candidate.unusable_reason && candidate.unusable_reason !== candidate.reason
                      ? ` ${candidate.unusable_reason}`
                      : ''}
                  </div>
                )}
              </div>
              <div className="row">
                {!candidate.usable && (
                  <button
                    className="btn quiet sm edit-candidate"
                    type="button"
                    disabled={busy}
                    onClick={() => onEdit(candidate.material_id)}
                  >
                    编辑摘录
                  </button>
                )}
                <button
                  className="btn quiet sm remove-candidate"
                  type="button"
                  disabled={busy}
                  aria-label={`移除 ${candidate.title}`}
                  onClick={(event) => remove(event, candidate.material_id)}
                >
                  移除
                </button>
              </div>
            </div>
          ))}
          {!selection.candidates.length && <p className="candidate-help">可以从收藏中选用这次需要的材料。</p>}
        </div>
        <CandidatePicker
          selection={selection}
          disabled={busy}
          materialChange={materialChange}
          onAdd={onAdd}
        />
        <div className="row" style={{ marginTop: 11 }}>
          <button
            className="btn primary sm confirm-materials"
            type="button"
            disabled={busy || !chosen.size}
            onClick={onConfirm}
          >
            {confirming ? '确认中…' : '确认这些材料'}
          </button>
          <span className="candidate-count candidate-help" role="status">
            {chosen.size
              ? `已选 ${chosen.size} 份，最多可用 ${selection.limit} 份`
              : '请选择至少一份能直接引用的材料'}
          </span>
        </div>
      </div>
      {selection.confirmed && (
        <div className="row candidate-confirmed">
          <span className="candidate-help">已确认 {chosen.size} 份材料</span>
          <button className="btn quiet sm" type="button" disabled={busy} onClick={onReopen}>
            重新选材
          </button>
        </div>
      )}
    </>
  )
}
