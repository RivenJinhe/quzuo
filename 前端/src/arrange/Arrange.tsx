import { Fragment, useEffect, useEffectEvent, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { api, ApiError, errorMessage, pollGeneration } from '../api'
import { getSelectedMaterials } from '../materials/materials'
import type {
  Conversation,
  ConversationCreated,
  Generation,
  GenerationSubmission,
  Material,
  ModelCatalog,
  Plan,
  RetrievedMaterials,
  TaskType,
  TurnResponse,
} from '../types'
import { Candidates } from './Candidates'
import { Result } from './Result'
import type { ModelState, ResultState } from './Result'
import { addMaterials, MATERIAL_LIMIT, prepareSelection, removeMaterials, updateMaterials } from './selection'
import type { ArrangeMaterialChange, Selection } from './selection'
export type { ArrangeMaterialChange } from './selection'

export type ArrangeLaunch = { kind: 'new' | 'add'; selectedIds: string[] } | { kind: 'resume'; plan: Plan }

interface ArrangeProps {
  selectedIds: string[]
  launch: ArrangeLaunch | null
  materialChange?: ArrangeMaterialChange | null
  toast: (message: string, bad?: boolean) => void
  onEnterArrange: () => void
  onOpenPlans: () => void
  onEditMaterial: (material: Material) => void
  onSelectionChange: (ids: string[]) => void
  onShowCitation: (id: string) => void
}

type Action =
  | 'goal'
  | 'confirm'
  | 'start'
  | 'turn'
  | 'resume'
  | 'retrieve'
  | 'add'
  | 'edit'
  | 'cancel'
  | 'save'
type Turn = Conversation['turns'][number]

const TASKS: { kind: TaskType; label: string; exampleLabel: string; example: string }[] = [
  {
    kind: 'review',
    label: '复习',
    exampleLabel: '复习备考',
    example: '这周三晚上复习两小时，重点放在最难的那一章',
  },
  {
    kind: 'travel',
    label: '出行',
    exampleLabel: '出门办事',
    example: '周六下午去市里，来回怎么安排最省时间',
  },
  {
    kind: 'activity',
    label: '活动',
    exampleLabel: '办个小活动',
    example: '下周办一场 40 人的活动，怎么排流程',
  },
]
const STEPS = ['说任务', '确认材料', '生成', '改与存']
const emptyResult: ResultState = { kind: 'empty' }

function AssistantCard({ children, kind }: { children: ReactNode; kind?: string }) {
  return (
    <div data-kind={kind}>
      <div className="role">
        <span className="ai" aria-hidden="true">
          智
        </span>
        助手 · 去做
      </div>
      <div className="acard">{children}</div>
    </div>
  )
}

function UserMessage({ text }: { text: string }) {
  return (
    <div className="msg-user">
      <div className="q" style={{ whiteSpace: 'pre-wrap' }}>
        {text}
      </div>
    </div>
  )
}

function modelIds(models: ModelState): string[] {
  if (models.kind !== 'ready') {
    throw new Error(models.kind === 'error' ? models.message : '正在读取模型…')
  }
  if (models.selected !== 'compare') return [models.selected]
  return models.catalog.items
    .filter((model) => model.available)
    .slice(0, models.catalog.compare_limit)
    .map((model) => model.id)
}

/** Owns one arrangement and keeps it available while the product switches screens. */
export default function Arrange({
  selectedIds,
  launch,
  materialChange,
  toast,
  onEnterArrange,
  onOpenPlans,
  onEditMaterial,
  onSelectionChange,
  onShowCitation,
}: ArrangeProps) {
  const [task, setTask] = useState<TaskType>('review')
  const [draft, setDraft] = useState('')
  const [goalAttempts, setGoalAttempts] = useState<{ text: string; error?: string }[]>([])
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [resumePlan, setResumePlan] = useState<Plan | null>(null)
  const [retrieveError, setRetrieveError] = useState('')
  const [turns, setTurns] = useState<(Turn & { retry?: boolean })[]>([])
  const [action, setAction] = useState<Action | null>(null)
  const [models, setModels] = useState<ModelState>({ kind: 'loading' })
  const [modelRetry, setModelRetry] = useState(0)
  const [result, setResult] = useState<ResultState>(emptyResult)
  const [polling, setPolling] = useState(false)
  const request = useRef<AbortController | null>(null)
  const poll = useRef<{ id: string; controller: AbortController } | null>(null)
  const generationId = useRef<string | null>(null)
  const lastSuccess = useRef<Generation | null>(null)
  const handledLaunch = useRef<ArrangeLaunch | null>(null)
  const composer = useRef<HTMLTextAreaElement>(null)
  const thread = useRef<HTMLDivElement>(null)
  const region = useRef<HTMLElement>(null)

  const awaitingMaterials =
    !!conversation && (conversation.status === 'retrieved' || selection?.confirmed === false)
  const succeeded = result.kind === 'generation' && result.generation.status === 'succeeded'
  const step = !conversation ? 1 : awaitingMaterials ? 2 : succeeded ? 4 : 3
  const canGenerate =
    !!conversation &&
    !awaitingMaterials &&
    !action &&
    !polling &&
    result.kind !== 'poll-error' &&
    models.kind === 'ready'
  const canSave =
    succeeded &&
    !action &&
    !awaitingMaterials &&
    result.generation.conversation_version === conversation?.version

  useEffect(() => {
    const controller = new AbortController()
    void api<ModelCatalog>('GET', '/models', undefined, controller.signal)
      .then((catalog) => {
        const available = catalog.items.filter((model) => model.available)
        const defaultModel = available.find((model) => model.id === catalog.default) ?? available[0]
        if (!defaultModel) throw new Error('服务端尚未配置可用模型')
        if (!controller.signal.aborted) setModels({ kind: 'ready', selected: defaultModel.id, catalog })
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setModels({ kind: 'error', message: errorMessage(error) })
      })
    return () => controller.abort()
  }, [modelRetry])

  useEffect(
    () => () => {
      request.current?.abort()
      poll.current?.controller.abort()
    },
    [],
  )

  useEffect(() => {
    if (!materialChange) return
    setSelection((current) => {
      if (!current) return current
      return materialChange.kind === 'deleted'
        ? removeMaterials(current, new Set(materialChange.ids))
        : updateMaterials(
            current,
            materialChange.kind === 'saved' ? [materialChange.material] : materialChange.materials,
          )
    })
  }, [materialChange])

  useEffect(() => {
    const list = thread.current
    if (list) list.scrollTop = list.scrollHeight
  }, [conversation?.id, goalAttempts, resumePlan, selection?.confirmed, turns.length])

  useEffect(() => {
    const pane = region.current
    if (!succeeded || !pane || !pane.getClientRects().length || window.innerWidth > 1080) return
    pane.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    })
  }, [succeeded])

  async function runAction(kind: Action, work: (signal: AbortSignal) => Promise<void>) {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setAction(kind)
    try {
      await work(controller.signal)
    } catch (error) {
      if (!controller.signal.aborted) toast(errorMessage(error), true)
    } finally {
      if (request.current === controller) {
        request.current = null
        if (!controller.signal.aborted) setAction(null)
      }
    }
  }

  function stopWatching() {
    poll.current?.controller.abort()
    poll.current = null
    generationId.current = null
    setPolling(false)
  }

  function watch(id: string) {
    if (poll.current?.id === id) return
    stopWatching()
    const controller = new AbortController()
    poll.current = { id, controller }
    generationId.current = id
    setPolling(true)
    setResult({ kind: 'submitted' })
    void pollGeneration(id, controller.signal, (generation) => {
      if (generation.status === 'succeeded') lastSuccess.current = generation
      setResult({ kind: 'generation', generation })
    })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setResult({ kind: 'poll-error', id, message: errorMessage(error) })
      })
      .finally(() => {
        if (poll.current?.controller === controller && !controller.signal.aborted) {
          poll.current = null
          setPolling(false)
        }
      })
  }

  function watchSubmission(submission: GenerationSubmission) {
    const id = submission.comparisons?.[0]?.generation_id ?? submission.id
    if (!id) throw new Error('服务器没有返回生成任务编号')
    if ((submission.comparisons?.length ?? 0) > 1) {
      toast(`已提交 ${submission.comparisons?.length} 个模型，当前显示所选的第一项`)
    }
    watch(id)
  }

  function startNew(ids: string[] = []) {
    if (request.current) {
      toast('这次安排正在处理，稍后再开新任务', true)
      return false
    }
    stopWatching()
    lastSuccess.current = null
    setConversation(null)
    setSelection(null)
    setResumePlan(null)
    setRetrieveError('')
    setGoalAttempts([])
    setTurns([])
    setTask('review')
    setDraft('')
    setResult(emptyResult)
    onSelectionChange(ids)
    toast(ids.length ? `已带 ${ids.length} 份材料进入新任务` : '已开一个新任务，说说这次要安排什么')
    return true
  }

  function addToSelection(materials: Material[]) {
    if (!selection || selection.confirmed) return
    const updated = addMaterials(selection, materials)
    setSelection((current) => {
      if (!current || current.confirmed) return current
      return current === selection ? updated.selection : addMaterials(current, materials).selection
    })
    if (updated.limitReached) toast(`每次安排最多选 ${selection.limit} 份材料`, true)
  }

  async function retrieve(current: Conversation, signal: AbortSignal) {
    setRetrieveError('')
    try {
      const data = await api<RetrievedMaterials>(
        'GET',
        `/conversations/${current.id}/retrieve`,
        undefined,
        signal,
      )
      if (!signal.aborted)
        setSelection(
          prepareSelection(data.candidates, current.material_ids, data.material_limit ?? MATERIAL_LIMIT),
        )
    } catch (error) {
      if (signal.aborted) return
      setRetrieveError(errorMessage(error))
      toast(errorMessage(error), true)
    }
  }

  async function resume(plan: Plan) {
    if (!plan.conversation_id) {
      toast('这份方案找不到原对话，可以开一个新任务', true)
      return
    }
    await runAction('resume', async (signal) => {
      const current = await api<Conversation>('POST', `/plans/${plan.id}/resume`, undefined, signal)
      if (signal.aborted) return
      stopWatching()
      lastSuccess.current = null
      setConversation(current)
      setSelection(null)
      setResumePlan(plan)
      setGoalAttempts([])
      setTurns([])
      setTask(current.task_type)
      setDraft('')
      onSelectionChange(current.material_ids)
      setResult(current.status === 'retrieved' ? emptyResult : { kind: 'resume' })
      onEnterArrange()
      if (current.status === 'retrieved') await retrieve(current, signal)
      else toast('已载入原对话')
    })
  }

  const handleLaunch = useEffectEvent((command: ArrangeLaunch) => {
    if (request.current) {
      toast('这次安排正在处理，请稍后再试', true)
      return
    }
    if (command.kind === 'resume') {
      void resume(command.plan)
      return
    }
    const ids = command.selectedIds
    const limit = selection?.limit ?? MATERIAL_LIMIT
    if (ids.length > limit) {
      toast(`每次安排最多选 ${limit} 份材料`, true)
      return
    }
    if (command.kind === 'new' || (conversation && (!selection || selection.confirmed))) {
      if (startNew(ids)) onEnterArrange()
    } else if (selection) {
      void runAction('add', async (signal) => {
        const materials = await getSelectedMaterials(ids, signal)
        if (!signal.aborted) {
          addToSelection(materials)
          onEnterArrange()
          window.requestAnimationFrame(() => {
            thread.current?.querySelector('[data-kind="candidates"]')?.scrollIntoView({ block: 'nearest' })
          })
        }
      })
    } else {
      onSelectionChange(ids)
      toast(`已带 ${ids.length} 份材料进入对话`)
      onEnterArrange()
    }
  })

  useEffect(() => {
    if (!launch || handledLaunch.current === launch) return
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      handledLaunch.current = launch
      handleLaunch(launch)
    })
    return () => {
      cancelled = true
    }
  }, [launch])

  async function sendGoal(text: string) {
    if (selectedIds.length > MATERIAL_LIMIT) {
      toast(`每次安排最多选 ${MATERIAL_LIMIT} 份材料`, true)
      return
    }
    await runAction('goal', async (signal) => {
      setDraft('')
      setGoalAttempts((previous) => [...previous, { text }])
      try {
        const data = await api<ConversationCreated>(
          'POST',
          '/conversations',
          {
            goal: text,
            task_type: task,
            material_ids: selectedIds,
            conditions: {},
          },
          signal,
        )
        if (signal.aborted) return
        setConversation(data.conversation)
        const prepared = prepareSelection(data.candidates, selectedIds, data.material_limit ?? MATERIAL_LIMIT)
        setSelection(prepared)
        if (prepared.candidates.length) {
          const usable = prepared.candidates.filter((candidate) => candidate.usable).length
          toast(`检索到 ${prepared.candidates.length} 份候选（可用 ${usable} 份）`)
        }
      } catch (error) {
        if (signal.aborted) return
        setDraft((previous) => previous || text)
        setGoalAttempts((previous) => [...previous.slice(0, -1), { text, error: errorMessage(error) }])
        throw error
      }
    })
  }

  async function confirmSelection() {
    if (!conversation || !selection || selection.confirmed || !selection.ids.length) return
    await runAction('confirm', async (signal) => {
      const materials = selection.ids.map((id) => ({ id, use: true }))
      const current = await api<Conversation>(
        'POST',
        `/conversations/${conversation.id}/materials/confirm`,
        { materials },
        signal,
      )
      if (signal.aborted) return
      stopWatching()
      setResult(emptyResult)
      setConversation(current)
      setSelection((previous) => {
        if (!previous) return previous
        const usable = new Set(
          previous.candidates
            .filter((candidate) => candidate.usable)
            .map((candidate) => candidate.material_id),
        )
        const ids = current.material_ids.filter((id) => usable.has(id))
        return { ...previous, ids, confirmed: ids.length === current.material_ids.length }
      })
      onSelectionChange(current.material_ids)
    })
  }

  async function startGeneration() {
    if (!canGenerate || !conversation) return
    await runAction('start', async (signal) => {
      setResult({ kind: 'submitted' })
      try {
        const submission = await api<GenerationSubmission>(
          'POST',
          `/conversations/${conversation.id}/generations`,
          {
            model_ids: modelIds(models),
          },
          signal,
        )
        if (!signal.aborted) watchSubmission(submission)
      } catch (error) {
        if (signal.aborted) return
        setResult({ kind: 'submission-error', message: errorMessage(error) })
        throw error
      }
    })
  }

  async function recoverTurn(path: string, text: string, signal: AbortSignal) {
    const current = await api<Conversation>('GET', path, undefined, signal)
    if (signal.aborted) return
    stopWatching()
    lastSuccess.current = null
    setConversation(current)
    if (current.status === 'retrieved') {
      setSelection(null)
      setResult(emptyResult)
      onSelectionChange(current.material_ids)
      await retrieve(current, signal)
      if (!signal.aborted) toast('已载入最新条件，请重新确认材料', true)
      return
    }
    const latest = current.turns.at(-1)
    if (latest?.version === current.version && latest.text === text) {
      const history = await api<{ items: Pick<Generation, 'id' | 'conversation_version'>[] }>(
        'GET',
        `${path}/generations`,
        undefined,
        signal,
      )
      if (signal.aborted) return
      const match = history.items.find((generation) => generation.conversation_version === current.version)
      setDraft((previous) => (previous.trim() === text ? '' : previous))
      setTurns((previous) => [...previous, { ...latest, retry: !match }])
      if (match) watch(match.id)
      else setResult({ kind: 'submission-error', message: '条件已保存，可以按这些条件生成。' })
      toast('这次调整已保存，已恢复对话')
      return
    }
    setResult({ kind: 'submission-error', message: '对话条件已有更新。' })
    toast('已载入最新条件，请核对后重发', true)
  }

  async function sendTurn(text: string) {
    if (!conversation || awaitingMaterials || !text) return
    await runAction('turn', async (signal) => {
      const path = `/conversations/${conversation.id}`
      try {
        const base = lastSuccess.current
        const response = await api<TurnResponse>(
          'POST',
          `${path}/turns`,
          {
            text,
            version: conversation.version,
            model_ids: modelIds(models),
            base_generation_id: base?.conversation_id === conversation.id ? base.id : undefined,
          },
          signal,
        )
        if (signal.aborted) return
        setDraft((previous) => (previous.trim() === text ? '' : previous))
        setConversation(response.conversation)
        setTurns((previous) => [
          ...previous,
          {
            ...response.turn,
            applied: response.applied,
            summary: response.summary,
            unresolved: response.unresolved,
            retry: !response.generation,
          },
        ])
        if (response.generation) watchSubmission(response.generation)
        else {
          stopWatching()
          setResult({ kind: 'submission-error', message: '条件已保存，可以重试生成。' })
        }
      } catch (error) {
        if (signal.aborted) return
        setDraft((previous) => previous || text)
        if (error instanceof ApiError && error.code === 'VERSION_CONFLICT')
          await recoverTurn(path, text, signal)
        else throw error
      }
    })
  }

  async function cancelGeneration() {
    const id = generationId.current
    if (!id) return
    await runAction('cancel', async (signal) => {
      const cancellation = await api<{ status: string }>(
        'POST',
        `/generations/${id}/cancel`,
        undefined,
        signal,
      )
      if (signal.aborted || generationId.current !== id) return
      if (cancellation.status === 'cancelled') toast('已取消')
      if (!poll.current) watch(id)
    })
  }

  async function savePlan(generation: Generation) {
    if (!canSave) return
    await runAction('save', async (signal) => {
      try {
        const saved = await api<Plan>(
          'POST',
          '/plans',
          {
            conversation_id: generation.conversation_id,
            generation_id: generation.id,
            title: generation.result?.headline?.trim().slice(0, 200) || '我的方案',
          },
          signal,
        )
        if (signal.aborted) return
        toast(`已保存（v${saved.version}）`)
        onOpenPlans()
      } catch (error) {
        if (error instanceof ApiError && error.code === 'STALE_GENERATION') {
          toast('这条结果已过期，请用最新一次生成保存', true)
        } else throw error
      }
    })
  }

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = draft.trim()
    if (!text) toast('先写一句要安排什么', true)
    else if (awaitingMaterials) toast('请先确认本次使用的材料', true)
    else if (conversation) void sendTurn(text)
    else void sendGoal(text)
  }

  function toggleCandidate(id: string, checked: boolean) {
    if (!selection || selection.confirmed || request.current) return
    if (checked && selection.ids.length >= selection.limit) {
      toast(`每次安排最多选 ${selection.limit} 份材料`, true)
      return
    }
    setSelection({
      ...selection,
      ids: checked ? [...selection.ids, id] : selection.ids.filter((candidateId) => candidateId !== id),
    })
  }

  return (
    <div className="workbench">
      <div className="stream">
        <div className="stream-head">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>
                Step 2 · 说任务，改条件
              </div>
              <h2 style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-.015em' }}>做个安排</h2>
            </div>
            <div className="row" style={{ gap: 8 }}>
              <div className="seg" role="group" aria-label="任务类型">
                {TASKS.map(({ kind, label }) => (
                  <button
                    type="button"
                    className={task === kind ? 'on' : ''}
                    key={kind}
                    aria-pressed={task === kind}
                    disabled={!!conversation || !!action}
                    onClick={() => setTask(kind)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <button className="btn sm" type="button" disabled={!!action} onClick={() => startNew()}>
                ＋ 新任务
              </button>
            </div>
          </div>
          <div className="steps" aria-label="安排进度">
            {STEPS.map((label, index) => (
              <Fragment key={label}>
                {index > 0 && <span className="step-sep" aria-hidden="true" />}
                <div
                  className={`step ${step === index + 1 ? 'now' : step > index + 1 ? 'done' : ''}`}
                  aria-current={step === index + 1 ? 'step' : undefined}
                >
                  <span className="n">{index + 1}</span>
                  {label}
                </div>
              </Fragment>
            ))}
          </div>
        </div>

        <div className="thread" ref={thread}>
          {!goalAttempts.length && !resumePlan && (
            <div className="empty" style={{ display: 'grid', margin: 'auto' }}>
              <div className="empty-ic" aria-hidden="true">
                说
              </div>
              <b>说一句要安排什么</b>
              <p>
                比如「这周三晚上复习两小时，重点放在最难的那一章」。我会先在你收藏的材料里找依据，确认后才生成。
              </p>
              <div className="row" style={{ justifyContent: 'center', flexWrap: 'wrap' }}>
                {TASKS.map(({ kind, example, exampleLabel }) => (
                  <button
                    className="btn sm"
                    type="button"
                    key={kind}
                    onClick={() => {
                      setTask(kind)
                      setDraft(example)
                      composer.current?.focus()
                    }}
                  >
                    {exampleLabel}
                  </button>
                ))}
              </div>
            </div>
          )}
          {goalAttempts.map((attempt, index) => (
            <Fragment key={index}>
              <UserMessage text={attempt.text} />
              {attempt.error && (
                <AssistantCard>
                  <h5>没能开始</h5>
                  <div className="notice bad">{attempt.error}</div>
                </AssistantCard>
              )}
            </Fragment>
          ))}
          {action === 'goal' && (
            <AssistantCard>
              <h5 role="status">正在检索你的材料…</h5>
              <div className="skel" style={{ width: '70%' }} aria-hidden="true" />
              <div className="skel" style={{ width: '90%', marginTop: 8 }} aria-hidden="true" />
            </AssistantCard>
          )}
          {resumePlan && conversation && (
            <AssistantCard>
              {conversation.status === 'retrieved' || selection ? (
                <h5>原材料已有变化，请重新确认本次采用的材料。</h5>
              ) : (
                <>
                  <h5>已回到这份方案原来的对话</h5>
                  <div style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.75 }}>
                    原目标：{conversation.goal}
                    <br />
                    已载入保存时的条件和 {conversation.material_ids.length} 份材料。
                    <br />
                    直接在下面说要改什么，比如「把复习时间压到 90 分钟」。
                  </div>
                  <div className="row" style={{ marginTop: 11 }}>
                    <button
                      className="btn primary sm"
                      type="button"
                      disabled={!canGenerate}
                      onClick={() => void startGeneration()}
                    >
                      按现在的条件重新生成 →
                    </button>
                  </div>
                </>
              )}
            </AssistantCard>
          )}
          {conversation && awaitingMaterials && !selection && (
            <AssistantCard>
              {retrieveError ? (
                <div className="row">
                  <span className="candidate-help" role="status">
                    {retrieveError}
                  </span>
                  <button
                    className="btn quiet sm"
                    type="button"
                    disabled={!!action}
                    onClick={() => void runAction('retrieve', (signal) => retrieve(conversation, signal))}
                  >
                    重试
                  </button>
                </div>
              ) : (
                <div className="candidate-help" role="status">
                  正在检索材料…
                </div>
              )}
            </AssistantCard>
          )}
          {selection && (
            <AssistantCard kind="candidates" key={conversation?.id}>
              <Candidates
                selection={selection}
                busy={!!action}
                confirming={action === 'confirm'}
                materialChange={materialChange}
                onToggle={toggleCandidate}
                onRemove={(id) => setSelection(removeMaterials(selection, new Set([id])))}
                onEdit={(id) =>
                  void runAction('edit', async (signal) => {
                    const material = await api<Material>('GET', `/materials/${id}`, undefined, signal)
                    if (!signal.aborted) onEditMaterial(material)
                  })
                }
                onAdd={(material) => addToSelection([material])}
                onConfirm={() => void confirmSelection()}
                onReopen={() => setSelection({ ...selection, confirmed: false })}
              />
            </AssistantCard>
          )}
          {selection?.confirmed && (
            <AssistantCard>
              <h5>可以开始生成方案</h5>
              <button
                className="btn primary sm"
                type="button"
                disabled={!canGenerate}
                onClick={() => void startGeneration()}
              >
                开始生成 →
              </button>
            </AssistantCard>
          )}
          {turns.map((turn) => (
            <Fragment key={turn.version}>
              <UserMessage text={turn.text} />
              <AssistantCard>
                <h5>
                  已记下这次调整 <span className="badge ok">v{turn.version}</span>
                </h5>
                <div style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.65 }}>
                  {turn.applied.length
                    ? `已应用：${turn.applied.map((change) => change.label || `${change.field}：${change.from} → ${change.to}`).join('；')}`
                    : turn.summary || '条件已更新'}
                  {!!turn.unresolved.length && `（未识别：${turn.unresolved.join('、')}）`}
                </div>
                {turn.retry && turn.version === conversation?.version && (
                  <div className="row" style={{ marginTop: 11 }}>
                    <button
                      className="btn primary sm"
                      type="button"
                      disabled={!canGenerate}
                      onClick={() => void startGeneration()}
                    >
                      重试生成 →
                    </button>
                  </div>
                )}
              </AssistantCard>
            </Fragment>
          ))}
        </div>

        <form className="composer" onSubmit={send}>
          <div className="composer-inner">
            <textarea
              id="composer"
              ref={composer}
              rows={1}
              maxLength={500}
              value={draft}
              aria-label={conversation ? '修改安排的条件' : '输入安排任务'}
              placeholder="改要求，比如「把复习时间压到 90 分钟」…"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  if (!action) event.currentTarget.form?.requestSubmit()
                }
              }}
            />
            <button className="btn primary sm" type="submit" style={{ height: 34 }} disabled={!!action}>
              发送 ↵
            </button>
          </div>
          <div className="hintrow">
            <span>生成中再改条件，以最后一次为准</span>
            <span className="mono">Shift + ↵ 换行</span>
          </div>
        </form>
      </div>

      <Result
        state={result}
        models={models}
        region={region}
        onModelChange={(value) =>
          setModels((current) => (current.kind === 'ready' ? { ...current, selected: value } : current))
        }
        onReloadModels={() => {
          setModels({ kind: 'loading' })
          setModelRetry((current) => current + 1)
        }}
        actions={{
          actionsDisabled: !!action || awaitingMaterials,
          canGenerate,
          canSave,
          cancelling: action === 'cancel',
          onStart: () => void startGeneration(),
          onCancel: () => void cancelGeneration(),
          onPollRetry: (id) => {
            if (!request.current) watch(id)
          },
          onSave: (generation) => void savePlan(generation),
          onSend: (text) => void sendTurn(text),
          onShowCitation,
        }}
      />
    </div>
  )
}
