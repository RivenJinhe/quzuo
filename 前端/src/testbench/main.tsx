import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { authenticateAccount, getSession, logoutAccount } from '../account/session'
import { api } from '../api'
import type { Account, Plan, TrialStatus } from '../types'
import { useToast } from '../ui/useToast'
import type { Notify } from '../ui/useToast'
import { ChatPanel, useChat } from './chat'
import { demoMaterial, MaterialsPanel, useMaterials } from './materials'
import { PlansPanel, resultToText, savePlan, usePlans } from './plans'
import { diagnosticMessage, Modal, Toast } from './ui'
import type { OpenModal } from './ui'
import './style.css'

type Tab = 'materials' | 'chat' | 'plans'
type Credentials = { username: string; password: string }
type Session = { account: Account; trial: TrialStatus | null }

const tabs: Record<Tab, string> = { materials: '① 材料库', chat: '② 生成方案', plans: '③ 我的方案' }

function hashTab(): Tab {
  const name = window.location.hash.slice(1)
  return name === 'chat' || name === 'plans' ? name : 'materials'
}

function AuthForm({
  submit,
}: {
  submit: (action: 'login' | 'register', credentials: Credentials) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const submitter = (event.nativeEvent as SubmitEvent).submitter
    const action = submitter?.id === 'au-register' ? 'register' : 'login'
    const data = new FormData(event.currentTarget)
    setBusy(true)
    try {
      await submit(action, {
        username: String(data.get('username') ?? '').trim(),
        password: String(data.get('password') ?? ''),
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="stack" onSubmit={send} aria-busy={busy}>
      <label>
        用户名
        <input
          id="au-name"
          name="username"
          autoComplete="username"
          required
          disabled={busy}
          placeholder="3-32 位字母数字下划线"
        />
      </label>
      <label>
        密码
        <input
          id="au-pass"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          disabled={busy}
          placeholder="注册至少 8 位"
        />
      </label>
      <div className="row">
        <button className="primary" id="au-login" disabled={busy}>
          登录
        </button>
        <button className="ghost" id="au-register" disabled={busy}>
          注册
        </button>
      </div>
      <div className="hint">注册或登录后可管理材料和方案。</div>
    </form>
  )
}

type WorkspaceProps = {
  readable: boolean
  writable: boolean
  demo: { requested: boolean; used: boolean }
  notify: Notify
  openModal: OpenModal
  closeModal: () => void
}

function Workspace({ readable, writable, demo, notify, openModal, closeModal }: WorkspaceProps) {
  const [controller] = useState(() => new AbortController())
  const signal = controller.signal
  const [tab, setTab] = useState(hashTab)
  const [modelError, setModelError] = useState('')
  const [demoRunning, setDemoRunning] = useState(() => writable && demo.requested && !demo.used)
  const chat = useChat(signal, notify)
  const materials = useMaterials(signal, notify)
  const plans = usePlans(signal, notify, openModal)
  const goalInput = useRef<HTMLTextAreaElement>(null)

  function switchTab(next: Tab) {
    setTab(next)
    if (window.location.hash !== `#${next}`) window.history.replaceState(null, '', `#${next}`)
    refreshTab(next)
  }

  function refreshTab(next: Tab) {
    if (!readable) return
    if (next === 'plans') void plans.refresh()
    if (next === 'materials') void materials.refresh()
  }

  async function runDemo(defaultModels: string[]) {
    const goal = '帮我用 15 分钟复习极限，先讲例题'
    const conditions = { duration_minutes: 15 }
    try {
      if (!defaultModels.length) throw new Error('默认模型不可用')
      notify('演示模式：自动走一遍流程…')
      const material = await materials.save(demoMaterial)
      materials.setSelected([material.id])
      await materials.refresh()
      chat.selectTask('review')
      chat.setGoal(goal)
      if (hashTab() !== 'plans') switchTab('chat')
      const created = await chat.start({ goal, task_type: 'review', material_ids: [material.id], conditions })
      const conversation = await chat.confirm(
        created.conversation,
        created.candidates.map((candidate) => ({ id: candidate.material_id, use: candidate.usable })),
      )
      const completions = await chat.generate(conversation, defaultModels)
      const result = await completions[0]
      if (signal.aborted) return
      if (result?.status !== 'succeeded') {
        notify(`演示：gen=${result?.status ?? 'none'} · 未保存`, true)
        return
      }
      try {
        await savePlan(
          {
            conversation_id: conversation.id,
            generation_id: result.id,
            title: '复习方案 · 15 分钟极限',
            body: resultToText(result.result),
            body_private_note: '例题那步还是有点绕',
            rationale: '按课件第 8 页顺序排',
            confirmed_conditions: conversation.conditions,
          },
          signal,
        )
        await plans.refresh()
        if (!signal.aborted) notify(`演示：gen=${result.status} · 已保存`)
      } catch (error) {
        if (!signal.aborted) notify(`演示：gen=${result.status} · 未保存 · ${diagnosticMessage(error)}`, true)
      }
    } catch (error) {
      if (!signal.aborted) notify(`演示失败：${diagnosticMessage(error)}`, true)
    } finally {
      setDemoRunning(false)
    }
  }

  useEffect(() => {
    if (readable) {
      void materials.refresh()
      void plans.refresh()
    }
    let demoTimer: number | undefined
    void chat
      .loadModels()
      .then(({ defaults }) => {
        if (signal.aborted || !demoRunning) return
        demoTimer = window.setTimeout(() => {
          demo.used = true
          void runDemo(defaults)
        }, 600)
      })
      .catch((error: unknown) => {
        if (signal.aborted) return
        const message = `读取模型失败：${diagnosticMessage(error)}`
        setModelError(message)
        setDemoRunning(false)
        notify(message, true)
      })

    function updateHash() {
      const next = hashTab()
      setTab(next)
      refreshTab(next)
    }
    window.addEventListener('hashchange', updateHash)
    return () => {
      window.clearTimeout(demoTimer)
      window.removeEventListener('hashchange', updateHash)
      controller.abort()
    }
    // Each account owns a mounted workspace and its requests.
  }, [])

  function sendMaterials() {
    if (!materials.selected.length) {
      notify('先在列表里勾选材料', true)
      return
    }
    switchTab('chat')
    window.requestAnimationFrame(() => goalInput.current?.focus())
    notify(`已带入 ${materials.selected.length} 条材料`)
  }

  function onSaved(plan: Plan) {
    closeModal()
    notify(`已保存（版本 v${plan.version}）`)
    void plans.refresh()
  }

  return (
    <>
      <nav className="tabs" aria-label="测试台">
        {(Object.entries(tabs) as [Tab, string][]).map(([id, label]) => (
          <button
            type="button"
            className={`tab${id === tab ? ' active' : ''}`}
            data-tab={id}
            aria-pressed={id === tab}
            aria-controls={`tab-${id}`}
            key={id}
            onClick={() => switchTab(id)}
          >
            {label}
          </button>
        ))}
        <span className="spacer" />
        <span className="hint" id="model-hint">
          {modelError || (chat.catalog && `默认 ${chat.catalog.default}`)}
        </span>
      </nav>
      <main>
        <MaterialsPanel
          materials={materials}
          active={tab === 'materials'}
          readable={readable}
          writable={writable && !demoRunning}
          signal={signal}
          notify={notify}
          send={sendMaterials}
        />
        <ChatPanel
          chat={chat}
          materialIds={materials.selected}
          active={tab === 'chat'}
          writable={writable}
          demoRunning={demoRunning}
          signal={signal}
          notify={notify}
          openModal={openModal}
          onSaved={onSaved}
          goalInput={goalInput}
        />
        <PlansPanel
          plans={plans}
          active={tab === 'plans'}
          readable={readable}
          writable={writable && !demoRunning}
        />
      </main>
    </>
  )
}

function Testbench() {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [identityError, setIdentityError] = useState('')
  const [loggingOut, setLoggingOut] = useState(false)
  const [modal, setModal] = useState<{ title: string; body: ReactNode } | null>(null)
  const { toast, notify } = useToast(3000, 6000)
  const demo = useRef({
    requested: new URLSearchParams(window.location.search).get('demo') === '1',
    used: false,
  })
  const trial = session?.trial
  const readable = Boolean(trial && (trial.readable ?? Date.parse(trial.purge_at) > Date.now()))
  const writable = readable && Boolean(trial?.writable)

  const openModal: OpenModal = (title, body) => setModal({ title, body })
  const closeModal = () => setModal(null)

  useEffect(() => {
    const controller = new AbortController()
    async function loadIdentity() {
      try {
        const auth = await getSession(controller.signal)
        if (auth.account && auth.logged_in) {
          const trial = await api<TrialStatus>('GET', '/me', undefined, controller.signal)
          setSession({ account: auth.account, trial })
        }
      } catch (error) {
        if (controller.signal.aborted) return
        const message = `读取登录状态失败：${diagnosticMessage(error)}`
        setIdentityError(message)
        notify(message, true)
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void loadIdentity()
    return () => controller.abort()
  }, [notify])

  async function authenticate(action: 'login' | 'register', credentials: Credentials) {
    try {
      const data = await authenticateAccount(action, credentials)
      closeModal()
      setIdentityError('')
      setSession(data)
      notify(action === 'login' ? '已登录' : '注册成功')
    } catch (error) {
      notify(diagnosticMessage(error), true)
    }
  }

  async function logout() {
    setLoggingOut(true)
    try {
      await logoutAccount()
      closeModal()
      setSession(null)
      notify('已登出')
    } catch (error) {
      notify(diagnosticMessage(error), true)
    } finally {
      setLoggingOut(false)
    }
  }

  const identity = session ? `已登录：${session.account.username}` : '未登录，请先登录或注册'
  const access =
    trial &&
    ` · ${writable ? `可写，剩余 ${trial.days_left} 天` : readable ? '只读（试用已到期）' : '数据不可访问'}`

  return (
    <>
      <header className="topbar">
        <div className="brand">
          去做<span className="ver">测试台</span>
        </div>
        <div className="who" id="who" role="status">
          {loading
            ? '加载中…'
            : identityError || (
                <>
                  {identity}
                  {access}
                </>
              )}
        </div>
        <div className="top-actions">
          {session ? (
            <button className="ghost" id="btn-logout" disabled={loggingOut} onClick={() => void logout()}>
              登出
            </button>
          ) : (
            <button
              className="ghost"
              id="btn-login"
              disabled={loading}
              onClick={() => openModal('登录 / 注册', <AuthForm submit={authenticate} />)}
            >
              登录 / 注册
            </button>
          )}
        </div>
      </header>
      {(!loading || identityError) && (
        <Workspace
          key={session?.account.id ?? 'guest'}
          readable={readable}
          writable={writable}
          demo={demo.current}
          notify={notify}
          openModal={openModal}
          closeModal={closeModal}
        />
      )}
      {modal ? (
        <Modal key={modal.title} title={modal.title} toast={toast} close={closeModal}>
          {modal.body}
        </Modal>
      ) : (
        <Toast toast={toast} />
      )}
    </>
  )
}

const root = document.getElementById('root')
if (!root) throw new Error('测试台缺少挂载节点')
createRoot(root).render(<Testbench />)
