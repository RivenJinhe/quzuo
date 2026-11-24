import { useCallback, useEffect, useState } from 'react'

export type Notify = (message: string, error?: boolean) => void
export interface ToastMessage {
  message: string
  error: boolean
}

export function useToast(normalMs = 2200, errorMs = 3600) {
  const [toast, setToast] = useState<ToastMessage | null>(null)
  const notify = useCallback<Notify>((message, error = false) => setToast({ message, error }), [])
  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), toast.error ? errorMs : normalMs)
    return () => clearTimeout(timer)
  }, [toast, normalMs, errorMs])
  return { toast, notify }
}
