import type { Generation } from './types'

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly data: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** Both entry points use the same cookie session and structured API errors. */
export async function api<T = void>(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const multipart = body instanceof FormData
  const response = await fetch('/api/v1' + path, {
    method,
    credentials: 'same-origin',
    headers: body !== undefined && !multipart ? { 'Content-Type': 'application/json' } : undefined,
    body: multipart ? body : JSON.stringify(body),
    signal,
  })
  if (response.status === 204) return undefined as T
  if (response.ok) return response.json() as Promise<T>
  const raw = await response.text()
  let data
  try {
    data = JSON.parse(raw)
  } catch {
    data = raw
  }
  const reason = data?.error ?? data
  const detail = reason?.message ?? data?.detail
  const message = typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : raw
  throw new ApiError(message || `HTTP ${response.status}`, response.status, reason?.code ?? '', data)
}

function waitForPoll(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted()
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', abort)
      resolve()
    }, 1200)
    function abort() {
      clearTimeout(timer)
      reject(signal.reason)
    }
    signal.addEventListener('abort', abort, { once: true })
  })
}

/** Poll an existing generation. The caller owns cancellation and may resume the same id. */
export async function pollGeneration(id: string, signal: AbortSignal, onUpdate?: (gen: Generation) => void) {
  for (let attempt = 0; attempt < 150; attempt++) {
    const generation = await api<Generation>('GET', '/generations/' + id, undefined, signal)
    signal.throwIfAborted()
    onUpdate?.(generation)
    if (generation.status !== 'pending' && generation.status !== 'running') return generation
    await waitForPoll(signal)
  }
  throw new Error('等待结果超时，可以继续查看同一次生成。')
}
