import { useEffect, useRef } from 'react'
import type { Notify } from './useToast'

export interface TextPreview {
  title: string
  text: string
  meta: string
  copy?: boolean
  url?: string | null
}

export function TextDialog({
  preview,
  close,
  toast,
}: {
  preview: TextPreview
  close: () => void
  toast: Notify
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const field = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = dialog.current!
    el.showModal()
    return () => el.close()
  }, [])
  async function copy() {
    let copied = false
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(preview.text)
        copied = true
      } catch {
        /* Fall back to selecting the visible public text. */
      }
    }
    if (!copied && field.current) {
      field.current.focus()
      field.current.select()
      try {
        copied = document.execCommand('copy')
      } catch {
        /* Leave the text selected for manual copying. */
      }
    }
    if (copied) close()
    toast(copied ? '已复制正文，已去除私人备注' : '已选中正文，可长按或按 Ctrl+C 复制', !copied)
  }
  return (
    <dialog ref={dialog} id="planDialog" aria-labelledby="dialogTitle" onCancel={close}>
      <h2 id="dialogTitle">{preview.title}</h2>
      <div>
        {preview.meta}{' '}
        {preview.url && /^https?:\/\//i.test(preview.url) && (
          <a href={preview.url} target="_blank" rel="noopener noreferrer">
            打开原网页
          </a>
        )}
      </div>
      <textarea
        ref={field}
        className="textarea"
        id="dialogText"
        aria-label="可选取的正文"
        value={preview.text}
        readOnly
      />
      <div className="row">
        {preview.copy && (
          <button className="btn primary" onClick={copy}>
            复制正文
          </button>
        )}
        <button className="btn" onClick={close}>
          关闭
        </button>
      </div>
    </dialog>
  )
}
