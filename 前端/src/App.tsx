import { useEffect, useRef, useState } from 'react'
import { api, errorMessage } from './api'
import { citationSource } from './format'
import { Auth, Brand } from './account/Auth'
import { getSession, logoutAccount } from './account/session'
import Arrange from './arrange/Arrange'
import type { ArrangeLaunch, ArrangeMaterialChange } from './arrange/Arrange'
import { Materials } from './materials/Collection'
import { MaterialEditor } from './materials/MaterialEditor'
import { Plans, usePlans } from './plans/Plans'
import { PlanDetail } from './plans/PlanDetail'
import type { Account, Citation, Material, PublicPlan } from './types'
import { TextDialog, type TextPreview } from './ui/Feedback'
import { useToast, type Notify } from './ui/useToast'

type Screen = 'collect' | 'arrange' | 'plans' | 'detail' | 'auth'
const pages = [
  { id: 'collect', title: '我的收藏', short: '收藏', icon: '收' },
  { id: 'arrange', title: '做个安排', short: '安排', icon: '安' },
  { id: 'plans', title: '我的方案', short: '方案', icon: '案' },
] as const

export default function App() {
  const [account, setAccount] = useState<Account | null>(null)
  const [checking, setChecking] = useState(true)
  const { toast, notify } = useToast()
  useEffect(() => {
    const controller = new AbortController()
    getSession(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setAccount(data.account)
      })
      .catch((e) => {
        if (!controller.signal.aborted) notify('读取登录状态失败：' + errorMessage(e), true)
      })
      .finally(() => {
        if (!controller.signal.aborted) setChecking(false)
      })
    return () => controller.abort()
  }, [notify])
  return (
    <>
      <Workspace
        key={account?.id ?? 'guest'}
        account={account}
        checking={checking}
        onAccount={setAccount}
        toast={notify}
      />
      <div
        role="status"
        aria-live="polite"
        className={`toast${toast ? ' show' : ''}${toast?.error ? ' err' : ''}`}
      >
        {toast?.message}
      </div>
    </>
  )
}

interface WorkspaceProps {
  account: Account | null
  checking: boolean
  onAccount: (account: Account | null) => void
  toast: Notify
}

/** Signed-in screens share selection and navigation. Changing account discards all private drafts. */
function Workspace({ account, checking, onAccount, toast }: WorkspaceProps) {
  const [screen, setScreen] = useState<Screen>(account ? 'collect' : 'auth')
  const [selected, setSelected] = useState<string[]>([])
  const [materialCount, setMaterialCount] = useState(0)
  const [revision, setRevision] = useState(0)
  const [editor, setEditor] = useState<Material | null | undefined>(undefined)
  const [materialChange, setMaterialChange] = useState<ArrangeMaterialChange>()
  const [launch, setLaunch] = useState<ArrangeLaunch | null>(null)
  const [planId, setPlanId] = useState('')
  const [preview, setPreview] = useState<TextPreview | null>(null)
  const previewRequest = useRef<AbortController | null>(null)
  const [loggingOut, setLoggingOut] = useState(false)
  const main = useRef<HTMLElement>(null)
  const plans = usePlans(!!account)
  useEffect(() => () => previewRequest.current?.abort(), [])
  useEffect(() => {
    main.current?.scrollTo(0, 0)
    window.scrollTo(0, 0)
  }, [screen])

  function navigate(next: Screen) {
    if (!account && next !== 'auth') {
      toast('请先注册或登录', true)
      return
    }
    previewRequest.current?.abort()
    setPreview(null)
    setEditor(undefined)
    setScreen(next)
    if (next === 'plans') plans.refresh()
  }
  function startArrange(intent: ArrangeLaunch) {
    setLaunch(intent)
  }
  async function logout() {
    if (loggingOut) return
    setLoggingOut(true)
    try {
      await logoutAccount()
      onAccount(null)
    } catch (e) {
      toast('退出失败：' + errorMessage(e), true)
    } finally {
      setLoggingOut(false)
    }
  }

  async function openPreview(load: (signal: AbortSignal) => Promise<TextPreview>) {
    previewRequest.current?.abort()
    const controller = new AbortController()
    previewRequest.current = controller
    try {
      const data = await load(controller.signal)
      if (!controller.signal.aborted) setPreview(data)
    } catch (e) {
      if (!controller.signal.aborted) toast(errorMessage(e), true)
    }
  }
  function showCitation(id: string) {
    if (!id) {
      toast('这条没有出处快照')
      return
    }
    void openPreview(async (signal) => {
      const c = await api<Citation>('GET', '/citations/' + id, undefined, signal)
      return {
        title: c.material_title || '材料出处',
        text: c.excerpt_snapshot,
        meta: citationSource(c),
        url: c.jump_url,
      }
    })
  }
  function copyPlan(id: string) {
    void openPreview(async (signal) => {
      const p = await api<PublicPlan>('POST', '/plans/' + id + '/copy', undefined, signal)
      return { title: '公开版预览', text: p.text, meta: p.title, copy: true }
    })
  }
  const activePage = screen === 'detail' ? 'plans' : screen
  const username = account?.username ?? '未登录'
  const sectionClass = (page: Screen) => (screen === page ? 'screen active' : 'screen')

  return (
    <>
      <div className="app">
        <aside className="sidebar">
          <Brand />
          <div className="nav-label">工作区</div>
          {pages.map((page) => (
            <button
              key={page.id}
              aria-label={page.title}
              title={page.title}
              className={'nav-item' + (activePage === page.id ? ' active' : '')}
              aria-current={activePage === page.id ? 'page' : undefined}
              onClick={() => navigate(page.id)}
            >
              <i className="dot" />
              <span className="lbl">{page.title}</span>
              <span className="count">
                {page.id === 'collect' ? materialCount : page.id === 'plans' ? plans.plans.length : '0'}
              </span>
            </button>
          ))}
          <div className="sidebar-foot">
            <button className="account" onClick={() => navigate('auth')}>
              <span className="avatar">{username.slice(0, 1).toUpperCase()}</span>
              <span className="account-text">
                <span className="account-name">{username}</span>
                <span className="account-sub">{account ? '已登录 · ' + username : '注册后开始使用'}</span>
              </span>
            </button>
            {account && (
              <button className="btn quiet sm logout" disabled={loggingOut} onClick={logout}>
                退出登录
              </button>
            )}
          </div>
        </aside>
        <main className="main" ref={main}>
          {!checking && <HomeHint />}
          {screen === 'auth' && (
            <section className="screen active" id="screen-auth">
              {checking ? (
                <div className="pad" role="status">
                  正在读取账号…
                </div>
              ) : (
                <Auth
                  account={account}
                  onLogin={(a) => {
                    onAccount(a)
                    setScreen('collect')
                  }}
                  onLogout={logout}
                  toast={toast}
                />
              )}
            </section>
          )}
          {account && (
            <>
              <section className={sectionClass('collect')} id="screen-collect">
                <Materials
                  active={screen === 'collect'}
                  accountName={username}
                  selectedIds={selected}
                  onSelectionChange={setSelected}
                  onSendToArrange={(ids) => startArrange({ kind: 'add', selectedIds: ids })}
                  onEdit={setEditor}
                  onMaterialsDeleted={(ids) => setMaterialChange({ kind: 'deleted', ids })}
                  onMaterialsUpdated={(materials) => setMaterialChange({ kind: 'updated', materials })}
                  revision={revision}
                  onCountChange={setMaterialCount}
                  onOpenAccount={() => navigate('auth')}
                  toast={toast}
                />
              </section>
              <section className={sectionClass('arrange')} id="screen-arrange">
                <Arrange
                  selectedIds={selected}
                  launch={launch}
                  onEnterArrange={() => navigate('arrange')}
                  onOpenPlans={() => navigate('plans')}
                  onEditMaterial={setEditor}
                  onSelectionChange={setSelected}
                  onShowCitation={showCitation}
                  materialChange={materialChange}
                  toast={toast}
                />
              </section>
              {screen === 'plans' && (
                <section className="screen active" id="screen-plans">
                  <Plans
                    list={plans}
                    toast={toast}
                    newTask={() => startArrange({ kind: 'new', selectedIds: [] })}
                    copy={copyPlan}
                    open={(id) => {
                      setPlanId(id)
                      navigate('detail')
                    }}
                  />
                </section>
              )}
              {screen === 'detail' && (
                <section className="screen active" id="screen-detail">
                  <PlanDetail
                    key={planId}
                    id={planId}
                    toast={toast}
                    back={() => navigate('plans')}
                    copy={copyPlan}
                    showCitation={showCitation}
                    onSaved={plans.refresh}
                    resume={(plan) => startArrange({ kind: 'resume', plan })}
                  />
                </section>
              )}
            </>
          )}
        </main>
      </div>
      {account && editor !== undefined && (
        <MaterialEditor
          key={editor?.id ?? 'new'}
          material={editor}
          onClose={() => setEditor(undefined)}
          toast={toast}
          onSave={(material) => {
            setMaterialChange({ kind: 'saved', material })
            setRevision((v) => v + 1)
          }}
        />
      )}
      {preview && <TextDialog preview={preview} close={() => setPreview(null)} toast={toast} />}
      <nav className="tabbar" aria-label="工作区">
        {pages.map((page) => (
          <button
            key={page.id}
            className={activePage === page.id ? 'on' : ''}
            aria-current={activePage === page.id ? 'page' : undefined}
            onClick={() => navigate(page.id)}
          >
            <span className="ic">{page.icon}</span>
            {page.short}
          </button>
        ))}
      </nav>
    </>
  )
}

function HomeHint() {
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    if (
      !matchMedia('(max-width: 720px)').matches ||
      matchMedia('(display-mode: standalone)').matches ||
      ('standalone' in navigator && navigator.standalone)
    )
      return
    try {
      if (localStorage.getItem('quzuo.home-hint')) return
      localStorage.setItem('quzuo.home-hint', 'seen')
    } catch {
      /* Storage is optional for the install hint. */
    }
    setVisible(true)
  }, [])
  return (
    visible && (
      <div id="homeHint">
        下次打开更方便：可在浏览器菜单选择“添加到主屏幕”。
        <button className="btn quiet sm" onClick={() => setVisible(false)}>
          关闭
        </button>
      </div>
    )
  )
}
