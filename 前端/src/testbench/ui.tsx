import { useLayoutEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { ApiError, errorMessage } from '../api'
import type { ToastMessage } from '../ui/useToast'

export type OpenModal = (title: string, body: ReactNode) => void

export function diagnosticMessage(error: unknown) {
  const message = errorMessage(error)
  return error instanceof ApiError && error.code ? `${message}  [${error.code}]` : message
}

export function Toast({ toast }: { toast: ToastMessage | null }) {
  return (
    toast && (
      <div className={`toast${toast.error ? ' err' : ''}`} id="toast" role={toast.error ? 'alert' : 'status'}>
        {toast.message}
      </div>
    )
  )
}

type ModalProps = {
  title: string
  children: ReactNode
  toast: ToastMessage | null
  close: () => void
}

export function Modal({ title, children, toast, close }: ModalProps) {
  const modal = useRef<HTMLDialogElement>(null)
  useLayoutEffect(() => {
    const dialog = modal.current
    if (!dialog) return
    dialog.showModal()
    return () => {
      dialog.close()
    }
  }, [])

  return (
    <dialog
      className="modal"
      id="modal"
      ref={modal}
      aria-labelledby="modal-title"
      onCancel={(event) => {
        event.preventDefault()
        close()
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) close()
      }}
    >
      <div className="modal-box">
        <div className="modal-head">
          <h3 id="modal-title">{title}</h3>
          <button className="ghost small" id="modal-close" onClick={close}>
            关闭
          </button>
        </div>
        <div id="modal-body" className="modal-body">
          {children}
        </div>
      </div>
      <Toast toast={toast} />
    </dialog>
  )
}
