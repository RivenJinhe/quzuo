import { useRef, useState } from 'react'
import type { RefObject } from 'react'
import { api } from '../api'
import type {
  Conversation,
  ConversationCreated,
  GenerationSubmission,
  ModelCatalog,
  Plan,
  TaskType,
  TurnResponse,
} from '../types'
import type { Notify } from '../ui/useToast'
import { taskLabels } from './plans'
import { GenerationCard, useResults } from './results'
import { diagnosticMessage } from './ui'
import type { OpenModal } from './ui'

const templates: Record<TaskType, Record<string, unknown>> = {
  review: { duration_minutes: 15 },
  travel: {
    start_time: '15:15',
    available_minutes: 40,
    budget_per_person: 80,
    people: 1,
    legs: [{ minutes: 15 }, { minutes: 15 }],
  },
  activity: {
    total_minutes: 25,
    available_minutes: 25,
    people: 20,
    fixed_furniture: { 桌椅: 20 },
    budget_per_person: 50,
    items: [
      { title: '开场', minutes: 2, must_keep: true },
      { title: '互访', minutes: 12, must_keep: true },
      { title: '猜词', minutes: 8 },
      { title: '收尾', minutes: 3 },
    ],
  },
}

const statuses: Record<string, string> = {
  active: '进行中',
  retrieved: '已检索',
  generating: '生成中',
  confirmed: '已确认',
  archived: '已归档',
  saved: '已保存',
}
const turnHelp = '会保留已确认的重点'

function conditionText(conditions: Record<string, unknown>) {
  return JSON.stringify(conditions, null, 2)
}

function readConditions(raw: string): Record<string, unknown> {
  if (!raw.trim()) return {}
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    throw new Error(`条件不是合法 JSON：${diagnosticMessage(error)}`, { cause: error })
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('条件必须是 JSON 对象')
  return parsed as Record<string, unknown>
}

type ConversationInput = {
  goal: string
  task_type: TaskType
  material_ids: string[]
  conditions: Record<string, unknown>
}

type MaterialChoice = { id: string; use: boolean }

export function useChat(signal: AbortSignal, notify: Notify) {
  const [taskType, setTaskType] = useState<TaskType>('review')
  const [goal, setGoal] = useState('')
  const [conditions, setConditions] = useState(() => conditionText(templates.review))
  const [conditionsDirty, setConditionsDirty] = useState(false)
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [candidates, setCandidates] = useState<ConversationCreated['candidates']>([])
  const [chosen, setChosen] = useState<string[]>([])
  const [turnText, setTurnText] = useState('')
  const [turnHint, setTurnHint] = useState(turnHelp)
  const [busy, setBusy] = useState(false)
  const current = useRef<Conversation | null>(null)
  const results = useResults(signal, notify)
  const models = catalog?.items.filter((model) => selected.includes(model.id)).map((model) => model.id) ?? []

  function selectTask(type: TaskType) {
    setTaskType(type)
    setConditions(conditionText(templates[type]))
    setConditionsDirty(false)
  }

  function editConditions(value: string) {
    setConditions(value)
    setConditionsDirty(true)
  }

  function selectConversation(next: Conversation | null) {
    current.current = next
    setConversation(next)
  }

  async function loadModels() {
    const data = await api<ModelCatalog>('GET', '/models', undefined, signal)
    const defaults = data.items
      .filter((model) => model.available && (model.default || model.id === data.default))
      .map((model) => model.id)
    setCatalog(data)
    setSelected(defaults)
    return { catalog: data, defaults }
  }

  function toggleModel(id: string) {
    setSelected((ids) => (ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id]))
  }

  function toggleCandidate(id: string, use: boolean) {
    setChosen((ids) => (use ? [...ids, id] : ids.filter((value) => value !== id)))
  }

  async function start(input: ConversationInput) {
    const data = await api<ConversationCreated>('POST', '/conversations', input, signal)
    selectConversation(data.conversation)
    setCandidates(data.candidates)
    setChosen(
      data.candidates.filter((candidate) => candidate.usable).map((candidate) => candidate.material_id),
    )
    setConditionsDirty(false)
    setTurnHint(turnHelp)
    const usable = data.candidates.filter((candidate) => candidate.usable).length
    if (data.candidates.length) {
      notify(`检索到 ${data.candidates.length} 条候选（可引用 ${usable} 条）`)
    } else {
      notify('没检索到匹配材料，检查一下目标关键词或先加材料', true)
    }
    return data
  }

  async function confirm(conv: Conversation, materials: MaterialChoice[]) {
    if (!materials.length) throw new Error('无候选材料可确认')
    const data = await api<Conversation>(
      'POST',
      `/conversations/${conv.id}/materials/confirm`,
      { materials },
      signal,
    )
    selectConversation(data)
    notify(`已确认 ${data.material_ids.length} 条材料`)
    return data
  }

  async function applyConditions(conv: Conversation) {
    if (!conditionsDirty || taskType !== conv.task_type) return conv
    const data = await api<Conversation>(
      'PATCH',
      `/conversations/${conv.id}/conditions`,
      {
        version: conv.version,
        conditions: readConditions(conditions),
      },
      signal,
    )
    selectConversation(data)
    setConditionsDirty(false)
    return data
  }

  async function refreshAfterGeneration(conv: Conversation) {
    if (signal.aborted || current.current !== conv) return
    try {
      const latest = await api<Conversation>('GET', `/conversations/${conv.id}`, undefined, signal)
      if (current.current === conv) selectConversation(latest)
    } catch (error) {
      if (!signal.aborted && current.current === conv) notify(diagnosticMessage(error), true)
    }
  }

  async function generate(conv: Conversation, modelIds: string[]) {
    if (!modelIds.length) throw new Error('至少选一个模型')
    const data = await api<GenerationSubmission>(
      'POST',
      `/conversations/${conv.id}/generations`,
      { model_ids: modelIds },
      signal,
    )
    const running = { ...conv, status: 'generating' }
    selectConversation(running)
    const completions = results.add(running, data)
    void Promise.all(completions).then(() => refreshAfterGeneration(running))
    return completions
  }

  async function turn(conv: Conversation) {
    const text = turnText.trim()
    if (!text) throw new Error('先说一句要改什么')
    const updated = await applyConditions(conv)
    const data = await api<TurnResponse>(
      'POST',
      `/conversations/${updated.id}/turns`,
      {
        text,
        version: updated.version,
        model_ids: models.length ? models : undefined,
      },
      signal,
    )
    selectConversation(data.conversation)
    if (taskType === data.conversation.task_type) setConditions(conditionText(data.conversation.conditions))
    setTurnText('')
    const labels = data.applied.map(
      (change) => change.label || `${change.field}: ${String(change.from)} → ${String(change.to)}`,
    )
    const unresolved = data.unresolved.length ? ` ｜未识别：${data.unresolved.join('、')}` : ''
    setTurnHint((labels.length ? `已应用 ${labels.join('；')}` : data.summary || '已记录') + unresolved)
    if (data.generation) {
      const completions = results.add(data.conversation, data.generation, data.comparisons)
      void Promise.all(completions).then(() => refreshAfterGeneration(data.conversation))
    }
    notify(data.generation ? '已按新条件重排' : '已记录本次追问')
  }

  function clear() {
    selectConversation(null)
    setCandidates([])
    setChosen([])
    results.clear()
    notify('已清空当前对话')
  }

  async function run(action: () => Promise<void>) {
    setBusy(true)
    try {
      await action()
    } catch (error) {
      if (!signal.aborted) notify(diagnosticMessage(error), true)
    } finally {
      setBusy(false)
    }
  }

  async function startFromForm(materialIds: string[]) {
    const trimmedGoal = goal.trim()
    if (!trimmedGoal) throw new Error('先写一句「我要什么」')
    await start({
      goal: trimmedGoal,
      task_type: taskType,
      conditions: readConditions(conditions),
      material_ids: materialIds,
    })
  }

  function requireConversation() {
    if (!conversation) throw new Error('先新建对话')
    return conversation
  }

  return {
    taskType,
    selectTask,
    goal,
    setGoal,
    conditions,
    editConditions,
    conversation,
    candidates,
    chosen,
    toggleCandidate,
    catalog,
    models,
    toggleModel,
    loadModels,
    results,
    start,
    confirm,
    generate,
    clear,
    busy,
    run,
    turnText,
    setTurnText,
    turnHint,
    startFromForm,
    confirmFromForm: async () => {
      await confirm(
        requireConversation(),
        candidates.map((candidate) => ({
          id: candidate.material_id,
          use: chosen.includes(candidate.material_id),
        })),
      )
    },
    generateFromForm: async () => {
      const conv = requireConversation()
      if (!models.length) throw new Error('至少选一个模型')
      await generate(await applyConditions(conv), models)
    },
    turnFromForm: () => turn(requireConversation()),
  }
}

type ChatPanelProps = {
  chat: ReturnType<typeof useChat>
  materialIds: string[]
  active: boolean
  writable: boolean
  demoRunning: boolean
  signal: AbortSignal
  notify: Notify
  openModal: OpenModal
  onSaved: (plan: Plan) => void
  goalInput: RefObject<HTMLTextAreaElement | null>
}

export function ChatPanel({
  chat,
  materialIds,
  active,
  writable,
  demoRunning,
  signal,
  notify,
  openModal,
  onSaved,
  goalInput,
}: ChatPanelProps) {
  const { conversation } = chat
  const locked = !writable || chat.busy || demoRunning
  const convSummary = conversation
    ? [
        `对话 ${conversation.id.slice(0, 8)}`,
        taskLabels[conversation.task_type],
        `材料 ${conversation.material_ids.length} 条`,
        statuses[conversation.status] ?? conversation.status,
        `v${conversation.version}`,
        conversation.turns.length > 0 && `追问 ${conversation.turns.length} 次`,
      ]
        .filter(Boolean)
        .join(' · ')
    : '还没有对话'

  return (
    <section className={`panel${active ? '' : ' hidden'}`} id="tab-chat" aria-label="生成方案">
      <div className="two-col wide-left">
        <div className="col-form">
          <h2>对话设置</h2>
          <div className="stack" aria-busy={chat.busy || demoRunning}>
            <label>
              任务类型
              <select
                id="task-type"
                value={chat.taskType}
                disabled={locked}
                onChange={(event) => chat.selectTask(event.currentTarget.value as TaskType)}
              >
                {Object.entries(taskLabels).map(([type, label]) => (
                  <option key={type} value={type}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              我要什么
              <textarea
                id="goal"
                ref={goalInput}
                rows={2}
                disabled={locked}
                placeholder="如：帮我安排 15 分钟高数复习"
                value={chat.goal}
                onChange={(event) => chat.setGoal(event.currentTarget.value)}
              />
            </label>
            <label>
              条件（JSON，可直接改）
              <textarea
                id="conditions"
                rows={7}
                spellCheck={false}
                disabled={locked}
                value={chat.conditions}
                onChange={(event) => chat.editConditions(event.currentTarget.value)}
              />
            </label>
            <div className="row">
              <button
                className="primary"
                id="btn-start"
                disabled={locked}
                onClick={() => void chat.run(() => chat.startFromForm(materialIds))}
              >
                新建对话 / 检索材料
              </button>
              <button
                className="ghost"
                id="btn-reset-conv"
                disabled={chat.busy || demoRunning}
                onClick={chat.clear}
              >
                清空对话
              </button>
            </div>
            {conversation && (
              <div id="conv-box" className="box">
                <div className="box-title">候选材料（勾选后确认）</div>
                <div id="candidates" className="list compact">
                  {chat.candidates.length === 0 && <div className="hint">无候选</div>}
                  {chat.candidates.map((candidate) => (
                    <div className="card" data-id={candidate.material_id} key={candidate.material_id}>
                      <div className="card-head">
                        <input
                          type="checkbox"
                          className="use"
                          aria-label={`使用${candidate.title}`}
                          checked={chat.chosen.includes(candidate.material_id)}
                          disabled={locked}
                          onChange={(event) =>
                            chat.toggleCandidate(candidate.material_id, event.currentTarget.checked)
                          }
                          style={{ width: 'auto', marginTop: 3 }}
                        />
                        <div style={{ flex: 1 }}>
                          <div className="t">
                            {candidate.title}
                            {!candidate.usable && <span className="badge bad">仅链接/不可用</span>}
                          </div>
                          <div className="card-meta">
                            {candidate.topic} · 得分 {candidate.score}
                            {candidate.reason && ` · ${candidate.reason}`}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                <button
                  className="primary small"
                  id="btn-confirm"
                  disabled={locked || !chat.candidates.length}
                  onClick={() => void chat.run(chat.confirmFromForm)}
                >
                  确认使用这些材料
                </button>
              </div>
            )}
            <div className="box">
              <div className="box-title">模型（可多选对比）</div>
              <div id="models" className="models">
                {chat.catalog?.items.map((model) => {
                  const selected = chat.models.includes(model.id)
                  return (
                    <button
                      type="button"
                      className={`model-chip${selected ? ' on' : ''}${model.available ? '' : ' off'}`}
                      data-id={model.id}
                      title={model.available ? undefined : model.reason || '不可用'}
                      key={model.id}
                      aria-pressed={selected}
                      disabled={!model.available || chat.busy || demoRunning}
                      onClick={() => chat.toggleModel(model.id)}
                    >
                      {model.id}
                      {!model.available && '（未配置）'}
                    </button>
                  )
                })}
              </div>
              <div className="row">
                <button
                  className="primary"
                  id="btn-generate"
                  disabled={locked}
                  onClick={() => void chat.run(chat.generateFromForm)}
                >
                  生成方案
                </button>
                <span className="hint" id="gen-hint" role="status">
                  {chat.results.active > 0 && `正在轮询 ${chat.results.active} 个模型…`}
                </span>
              </div>
            </div>
            <div className="box">
              <label htmlFor="turn-text" className="box-title">
                继续说一句（自然语言改条件）
              </label>
              <textarea
                id="turn-text"
                rows={2}
                placeholder="例：时间只剩 5 分钟了，例题一定要讲"
                disabled={locked}
                value={chat.turnText}
                onChange={(event) => chat.setTurnText(event.currentTarget.value)}
              />
              <div className="row">
                <button
                  className="primary small"
                  id="btn-turn"
                  disabled={locked}
                  onClick={() => void chat.run(chat.turnFromForm)}
                >
                  发送并重排
                </button>
                <span className="hint" id="turn-hint" role="status">
                  {chat.turnHint}
                </span>
              </div>
            </div>
          </div>
        </div>
        <div className="col-list">
          <div className="toolbar">
            <span className="hint" id="conv-state" role="status">
              {convSummary}
            </span>
            <span className="spacer" />
            <button
              className="ghost"
              id="btn-clear-results"
              disabled={demoRunning}
              onClick={chat.results.clear}
            >
              清空结果
            </button>
          </div>
          <div id="results">
            {chat.results.results.map((result) => (
              <GenerationCard
                key={result.id}
                result={result}
                writable={writable && !demoRunning}
                cancel={chat.results.cancel}
                signal={signal}
                notify={notify}
                openModal={openModal}
                onSaved={onSaved}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
