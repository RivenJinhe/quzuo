import { api } from '../api'
import type { Account, AuthStatus, TrialStatus } from '../types'

export function getSession(signal?: AbortSignal) {
  return api<AuthStatus>('GET', '/auth/me', undefined, signal)
}

export function authenticateAccount(
  mode: 'login' | 'register',
  credentials: { username: string; password: string },
  signal?: AbortSignal,
) {
  return api<{ account: Account; trial: TrialStatus }>('POST', '/auth/' + mode, credentials, signal)
}

export async function logoutAccount(signal?: AbortSignal) {
  await api('POST', '/auth/logout', undefined, signal)
}
