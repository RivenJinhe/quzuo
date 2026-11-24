import { useEffect, useEffectEvent, useState } from 'react'
import { materialUrl } from './materials'

export type MaterialInput = { kind: 'file'; file: File } | { kind: 'url'; url: string }
export type MaterialDrop = MaterialInput | { kind: 'invalid'; message: string }

function hasFileOrLink(data: DataTransfer) {
  return data.types.includes('Files') || data.types.includes('text/uri-list')
}

function readDrop(data: DataTransfer): MaterialDrop | null {
  const fileItems = Array.from(data.items).filter((item) => item.kind === 'file')
  if (fileItems.some((item) => item.webkitGetAsEntry?.()?.isDirectory)) {
    return { kind: 'invalid', message: '请打开文件夹，拖入其中一个文件。' }
  }
  if (data.files.length > 1 || fileItems.length > 1) {
    return { kind: 'invalid', message: '一次可添加一个文件，请重新选择。' }
  }
  if (data.files[0]) return { kind: 'file', file: data.files[0] }

  const links = data.getData('text/uri-list').split(/\r?\n/)
    .map((line) => line.trim()).filter((line) => line && !line.startsWith('#'))
  if (links.length > 1) return { kind: 'invalid', message: '一次可添加一个网址，请重新选择。' }
  const plain = data.getData('text/plain').trim()
  const link = links[0] || (/^(?:https?:\/\/|www\.)\S+$/i.test(plain) ? plain : '')
  if (link) {
    const url = materialUrl(link)
    return url ? { kind: 'url', url } : { kind: 'invalid', message: '请拖入有效的 HTTP 或 HTTPS 网址。' }
  }
  if (hasFileOrLink(data)) {
    return { kind: 'invalid', message: '浏览器未收到可读文件或网址。可以先下载文件，再拖入或选择。' }
  }
  return null
}

/** Keeps browser navigation from replacing the workspace when a file or link is dropped. */
export function useMaterialDrop(enabled: boolean, onDrop: (drop: MaterialDrop) => void) {
  const [dragging, setDragging] = useState(false)
  const receive = useEffectEvent(onDrop)

  useEffect(() => {
    const listeners = new AbortController()
    let dragDepth = 0
    let internalDrag = false

    function reset() {
      dragDepth = 0
      setDragging(false)
    }

    function start() {
      internalDrag = true
    }

    function end() {
      internalDrag = false
      reset()
    }

    function enter(event: DragEvent) {
      if (!enabled || internalDrag || !event.dataTransfer || !hasFileOrLink(event.dataTransfer)) return
      event.preventDefault()
      dragDepth += 1
      setDragging(true)
    }

    function leave() {
      if (dragDepth > 0 && --dragDepth === 0) reset()
    }

    function over(event: DragEvent) {
      const data = event.dataTransfer
      if (!data || internalDrag) return
      if (!hasFileOrLink(data) && !(enabled && data.types.includes('text/plain'))) return
      event.preventDefault()
      if (!enabled) data.dropEffect = 'none'
      else data.dropEffect = ['link', 'linkMove'].includes(data.effectAllowed) ? 'link' : 'copy'
    }

    function drop(event: DragEvent) {
      reset()
      const data = event.dataTransfer
      if (!data || internalDrag) return
      if (hasFileOrLink(data)) event.preventDefault()
      if (!enabled) return
      const input = readDrop(data)
      if (!input) return
      event.preventDefault()
      receive(input)
    }

    const options = { signal: listeners.signal }
    window.addEventListener('dragstart', start, options)
    window.addEventListener('dragend', end, options)
    window.addEventListener('dragenter', enter, options)
    window.addEventListener('dragleave', leave, options)
    window.addEventListener('dragover', over, options)
    window.addEventListener('drop', drop, options)
    window.addEventListener('blur', end, options)
    return () => {
      listeners.abort()
      reset()
    }
  }, [enabled])

  return enabled && dragging
}

export function MaterialDropHint() {
  return <div className="material-drop-hint" aria-hidden="true">松开以添加材料</div>
}
