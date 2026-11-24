import { useState } from 'react'
import type { FormEvent } from 'react'
import { errorMessage } from '../api'
import { authenticateAccount } from './session'
import type { Account } from '../types'
import type { Notify } from '../ui/useToast'

export function Brand() {
  return (
    <div className="brand">
      <div className="brand-mark">去</div>
      <div className="brand-text">
        <div className="brand-name">去做</div>
        <div className="brand-sub">Knowledge → Plan</div>
      </div>
    </div>
  )
}

export function Auth({
  onLogin,
  toast,
  account,
  onLogout,
}: {
  onLogin: (account: Account) => void
  toast: Notify
  account: Account | null
  onLogout: () => void
}) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [busy, setBusy] = useState(false)
  const register = mode === 'register'
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const form = new FormData(event.currentTarget)
    const username = String(form.get('username')).trim()
    const password = String(form.get('password'))
    setBusy(true)
    try {
      const data = await authenticateAccount(mode, { username, password })
      onLogin(data.account)
      toast(register ? '注册成功' : '已登录')
    } catch (error) {
      toast(errorMessage(error), true)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="auth">
      <div className="auth-left">
        <Brand />
        <div className="auth-quote">
          先<em>记住</em>，<br />再<em>安排</em>。
        </div>
        <p className="auth-intro">
          把课件、通知和手记放进自己的知识库，说一句「这周三晚上复习两小时」，就能得到一份每句都有出处的安排。
        </p>
        <div className="auth-points">
          <div className="auth-point">
            <i>1</i>
            <div>
              <b>资料能用进方案</b> · 只检索你自己收的材料，不联网猜
            </div>
          </div>
          <div className="auth-point">
            <i>2</i>
            <div>
              <b>改要求，方案跟着改</b> · 说得越具体，排得越准
            </div>
          </div>
          <div className="auth-point">
            <i>3</i>
            <div>
              <b>有据可查</b> · 每条安排都能点回课件页码或原网页
            </div>
          </div>
        </div>
        <div className="auth-footnote">
          <span>注册后使用</span>
          <span>·</span>
          <span>数据只在你账号内</span>
          <span>·</span>
          <span>v0.2.4</span>
        </div>
      </div>
      <div className="auth-right">
        <form className="auth-card" onSubmit={submit}>
          <div className="auth-tabs">
            {(['login', 'register'] as const).map((value) => (
              <button
                type="button"
                key={value}
                aria-pressed={mode === value}
                className={mode === value ? 'on' : ''}
                disabled={busy}
                onClick={() => setMode(value)}
              >
                {value === 'login' ? '登录' : '注册'}
              </button>
            ))}
          </div>
          <div className="field">
            <label htmlFor="authUser">
              用户名 <span className="hint">3–32 位，字母数字下划线</span>
            </label>
            <input
              className="input"
              id="authUser"
              name="username"
              autoComplete="username"
              placeholder="起一个用户名"
              required
              minLength={3}
              maxLength={register ? 32 : 64}
              pattern={register ? '[A-Za-z0-9_]+' : undefined}
              disabled={busy}
            />
          </div>
          <div className="field">
            <label htmlFor="authPass">
              密码 <span className="hint">至少 8 位</span>
            </label>
            <input
              className="input"
              id="authPass"
              name="password"
              type="password"
              placeholder="至少 8 位"
              required
              minLength={8}
              maxLength={128}
              autoComplete={register ? 'new-password' : 'current-password'}
              disabled={busy}
            />
          </div>
          <button className="btn primary auth-submit" disabled={busy}>
            {busy ? '处理中…' : register ? '注册并开始使用' : '登录并继续'}
          </button>
          {account && (
            <button className="btn quiet" type="button" disabled={busy} onClick={onLogout}>
              退出 {account.username}
            </button>
          )}
          <div className="trial-note">
            <span>ⓘ</span>
            <div>必须注册或登录才能使用。账号内的材料与方案只有你自己能看，模型密钥只存在服务端。</div>
          </div>
          <div className="auth-terms">
            继续即表示同意「数据只用于生成你的方案」
            <br />
            模型密钥只存在服务端
          </div>
        </form>
      </div>
    </div>
  )
}
