import type { Ref } from 'react'
import type { Generation, ModelCatalog } from '../types'

export type ResultState =
  | { kind: 'empty' | 'resume' | 'submitted' }
  | { kind: 'submission-error'; message: string }
  | { kind: 'poll-error'; id: string; message: string }
  | { kind: 'generation'; generation: Generation }

export type ModelState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; catalog: ModelCatalog; selected: string }

const CHECK_LABELS: Record<string, string> = {
  STRUCTURE: '方案格式',
  CITATION_GATE: '原文出处',
  NUMBER_WHITELIST: '数字依据',
  DURATION_TOTAL: '时长',
  RETAINED_COVERED: '必留环节',
  MATERIAL_DERIVE: '物料',
  BUDGET: '预算',
  TIMELINE: '到离时间',
}

const CHANGE_GROUPS = [
  { title: '时长', kinds: ['duration', 'item_duration'] },
  { title: '环节', kinds: ['added', 'removed'] },
  { title: '物料', kinds: ['material'] },
]

function Options({
  options,
  disabled,
  onSend,
}: {
  options: Generation['options']
  disabled: boolean
  onSend: (text: string) => void
}) {
  return (
    <div className="opts">
      {(options ?? [])
        .filter((option) => option.send || option.label)
        .map((option, index) => (
          <button
            className="opt"
            type="button"
            disabled={disabled}
            key={index}
            onClick={() => onSend(option.send || option.label || '')}
          >
            <b>{option.label || option.send}</b>
            {option.effect && <span>{option.effect}</span>}
          </button>
        ))}
    </div>
  )
}

function Review({ validation }: { validation: Generation['validation'] }) {
  const checks = validation?.checks ?? []
  if (!checks.length) return null

  const notices = checks.filter((check) => check.level === 'notice').length
  const failures = checks.filter((check) => check.level !== 'notice' && check.ok === false).length
  return (
    <>
      <div style={{ fontSize: 11, color: 'var(--faint)', marginBottom: 7 }}>
        复核 · 通过 {checks.length - notices - failures} · 提示 {notices} · 未通过 {failures}
      </div>
      <div className="checks">
        {checks.map((check, index) => {
          const status = check.level === 'notice' ? 'warn' : check.ok === false ? 'block' : 'pass'
          return (
            <span className={`ck ${status}`} key={index}>
              {CHECK_LABELS[check.code] || '方案检查'} ·{' '}
              {{ warn: '有提示', block: '未通过', pass: '通过' }[status]}
            </span>
          )
        })}
      </div>
      {validation?.notices?.map((notice, index) => {
        const text = notice.message || (typeof notice.detail === 'string' ? notice.detail : '')
        return (
          text && (
            <div className="notice warn" key={index}>
              <i aria-hidden="true">!</i>
              <div>{text}</div>
            </div>
          )
        )
      })}
    </>
  )
}

interface ResultActions {
  actionsDisabled: boolean
  canGenerate: boolean
  canSave: boolean
  cancelling: boolean
  onStart: () => void
  onCancel: () => void
  onPollRetry: (id: string) => void
  onSave: (generation: Generation) => void
  onSend: (text: string) => void
  onShowCitation: (id: string) => void
}

function GenerationResult({ generation, actions }: { generation: Generation; actions: ResultActions }) {
  const { status, result, changes, result_sections: sections, validation, usage } = generation
  const retry = (
    <button className="btn" type="button" disabled={!actions.canGenerate} onClick={actions.onStart}>
      重新生成
    </button>
  )
  const options = (
    <Options options={generation.options} disabled={actions.actionsDisabled} onSend={actions.onSend} />
  )

  if (status === 'pending' || status === 'running') {
    const elapsed = Math.max(0, Math.floor((Date.now() - Date.parse(generation.created_at)) / 1000))
    return (
      <div style={{ display: 'grid', placeItems: 'center', textAlign: 'center', padding: '30px 0' }}>
        <div aria-hidden="true" style={{ fontSize: 24, color: 'var(--faint)' }}>
          生
        </div>
        <b role="status" style={{ fontSize: 13.5, marginTop: 8 }}>
          模型正在生成…
        </b>
        <p style={{ fontSize: 12, color: 'var(--muted)' }}>
          {status === 'pending' ? '排队中' : '生成中'} · 已用 {elapsed}s
        </p>
        <button
          className="btn quiet sm"
          type="button"
          style={{ marginTop: 10 }}
          disabled={actions.cancelling}
          onClick={actions.onCancel}
        >
          {actions.cancelling ? '正在取消…' : '取消生成'}
        </button>
      </div>
    )
  }
  if (status === 'superseded') {
    return (
      <div className="notice warn" role="status">
        <i aria-hidden="true">↻</i>
        <div>这条结果已作废。请查看最新一版。</div>
      </div>
    )
  }
  if (status !== 'succeeded') {
    const message =
      generation.error_message ||
      validation?.block?.message ||
      (status === 'cancelled' ? '已取消生成' : '这份方案未能生成')
    return (
      <>
        <div className="notice bad" role="status">
          {message}
        </div>
        {options}
        {retry}
      </>
    )
  }

  const items = result?.items ?? []
  const citationCount = items.reduce((total, item) => total + (item.citations?.length ?? 0), 0)
  const cost =
    usage?.cost_cny == null ? '费用未知' : `约 ¥${usage.cost_cny.toFixed(usage.cost_cny < 0.01 ? 4 : 3)}`
  return (
    <>
      {!!changes?.length && (
        <div className="changes">
          <div className="ch-head">本次改动影响</div>
          {CHANGE_GROUPS.map((group) => {
            const summary = changes
              .filter((change) => group.kinds.includes(change.kind))
              .map((change) => change.label)
              .join('，')
            return (
              <div className="ch" key={group.title}>
                <strong>{group.title}</strong>　{summary || '与上版相同'}
              </div>
            )
          })}
        </div>
      )}
      <div className="gen-title">{result?.headline || '已生成'}</div>
      {!!items.length && (
        <div>
          {items.map((item, index) => (
            <div className="item" key={index}>
              <div className="idx">{String(index + 1).padStart(2, '0')}</div>
              <div>
                <div className="h">
                  {item.title}
                  {item.minutes != null && <span className="mins">{item.minutes} min</span>}
                  {item.must_keep && <span className="must">必保留</span>}
                </div>
                <div className="d">{item.detail}</div>
                {!!item.citations?.length && (
                  <div style={{ marginTop: 6 }}>
                    {item.citations.map((citation, citeIndex) => (
                      <button
                        className="cite-link"
                        type="button"
                        key={citeIndex}
                        onClick={() => actions.onShowCitation(citation.citation_id || citation.id || '')}
                      >
                        出处 {citeIndex + 1}
                        {citation.page ? ` · 第 ${citation.page} 页` : ''}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {sections?.map((section, index) => (
        <section className="blk" key={index}>
          <b>{section.title}</b>
          <div style={{ whiteSpace: 'pre-line' }}>{section.lines.join('\n')}</div>
          {section.sources?.map((source, sourceIndex) => (
            <button
              className="cite-link"
              type="button"
              key={sourceIndex}
              onClick={() => actions.onShowCitation(source.id)}
            >
              {source.label} · 出处
            </button>
          ))}
        </section>
      ))}
      {options}
      <Review validation={validation} />
      <div className="row" style={{ gap: 8, marginTop: 6 }}>
        <button
          className="btn primary"
          type="button"
          style={{ flex: 1 }}
          disabled={!actions.canSave}
          onClick={() => actions.onSave(generation)}
        >
          保存为方案
        </button>
        {retry}
      </div>
      {!!(usage?.total_tokens || usage?.elapsed_ms) && (
        <div style={{ fontSize: 11, color: 'var(--faint)', marginTop: 10 }}>
          本次生成 {((usage.elapsed_ms || 0) / 1000).toFixed(1)} 秒 · {usage.model} ·{' '}
          {usage.total_tokens || 0} tokens · {cost}
        </div>
      )}
      <div
        style={{
          fontSize: 11,
          color: 'var(--faint)',
          display: 'flex',
          justifyContent: 'space-between',
          marginTop: 8,
        }}
      >
        <span>复制不含私人备注与选择理由</span>
        <span>{citationCount} 条出处</span>
      </div>
    </>
  )
}

export function Result({
  state,
  models,
  actions,
  region,
  onModelChange,
  onReloadModels,
}: {
  state: ResultState
  models: ModelState
  actions: ResultActions
  region: Ref<HTMLElement>
  onModelChange: (value: string) => void
  onReloadModels: () => void
}) {
  const available = models.kind === 'ready' ? models.catalog.items.filter((model) => model.available) : []
  const compareCount = models.kind === 'ready' ? Math.min(available.length, models.catalog.compare_limit) : 0
  const isEmpty = state.kind === 'empty' || state.kind === 'resume'
  return (
    <aside className="result" ref={region} aria-label="生成结果">
      <div className="result-head">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <label style={{ minWidth: 0 }}>
            生成模型
            <select
              id="modelSelect"
              className="input"
              disabled={models.kind !== 'ready'}
              value={models.kind === 'ready' ? models.selected : ''}
              onChange={(event) => onModelChange(event.target.value)}
            >
              {available.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.model}
                </option>
              ))}
              {compareCount > 1 && (
                <option value="compare">同时请求 {compareCount} 个模型（显示首个结果）</option>
              )}
              {models.kind !== 'ready' && (
                <option value="">{models.kind === 'loading' ? '正在读取模型…' : models.message}</option>
              )}
            </select>
          </label>
          {models.kind === 'error' && (
            <button type="button" className="btn quiet sm" onClick={onReloadModels}>
              重新读取
            </button>
          )}
        </div>
      </div>
      <div
        className="result-body"
        style={isEmpty ? { placeItems: 'center', textAlign: 'center' } : { display: 'block' }}
      >
        {isEmpty && (
          <div style={{ padding: '0 22px' }}>
            <div aria-hidden="true" style={{ fontSize: 24, color: 'var(--faint)', marginBottom: 10 }}>
              {state.kind === 'resume' ? '改' : '排'}
            </div>
            <b style={{ fontSize: 13.5 }}>
              {state.kind === 'resume' ? '说一句要改什么' : '生成结果会出现在这里'}
            </b>
            <p
              style={{
                fontSize: 12,
                color: 'var(--muted)',
                lineHeight: 1.75,
                maxWidth: '30ch',
                margin: '9px auto 0',
              }}
            >
              {state.kind === 'resume'
                ? '改完会自动重新生成一版，本次改动影响会写在结果最上面。'
                : '左边说清任务、确认材料后开始生成。右栏会显示分段时间表、八项复核和每条的出处。'}
            </p>
          </div>
        )}
        {state.kind === 'submitted' && (
          <div className="empty" role="status" style={{ display: 'grid' }}>
            正在生成方案…
          </div>
        )}
        {state.kind === 'submission-error' && (
          <>
            <div className="notice bad" role="status">
              {state.message}
            </div>
            <button className="btn" type="button" disabled={!actions.canGenerate} onClick={actions.onStart}>
              重试生成
            </button>
          </>
        )}
        {state.kind === 'poll-error' && (
          <>
            <div className="notice warn" role="status">
              {state.message}
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <button
                className="btn"
                type="button"
                disabled={actions.cancelling}
                onClick={() => actions.onPollRetry(state.id)}
              >
                继续查看结果
              </button>
              <button
                className="btn quiet sm"
                type="button"
                disabled={actions.cancelling}
                onClick={actions.onCancel}
              >
                {actions.cancelling ? '正在取消…' : '取消生成'}
              </button>
            </div>
          </>
        )}
        {state.kind === 'generation' && <GenerationResult generation={state.generation} actions={actions} />}
      </div>
    </aside>
  )
}
