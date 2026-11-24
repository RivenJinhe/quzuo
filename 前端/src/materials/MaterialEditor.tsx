import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, errorMessage } from '../api'
import type { ExtractedMaterial, Material } from '../types'
import { MaterialStatus } from './MaterialStatus'
import { initialFields, mainLink, materialDraft } from './materialDraft'
import type { EditorFields, WebExcerpt } from './materialDraft'
import { MaterialDropHint, type MaterialDrop, type MaterialInput } from './materialDrop'
import { materialUrl } from './materials'

const notePrompts = new Map([
  ['复习', '哪里没弄懂？'],
  ['出行', '想什么时候去？'],
  ['活动', '适合多少人？'],
  ['其他', '你为什么想存这份材料？'],
])
const fileTypes = '.pdf,.docx,.pptx,.md,.markdown,.txt,.csv'

type LinkedMaterial = (WebExcerpt & { kind: 'web' }) | { kind: 'file'; file: ExtractedMaterial }

function fetchMaterial(url: string, signal: AbortSignal) {
  return api<LinkedMaterial>('POST', '/materials/fetch', { url }, signal)
}

interface WebRead {
  key: string
  message: string
  result?: WebExcerpt
  controller?: AbortController
  promise?: Promise<void>
}

interface MaterialEditorProps {
  material: Material | null
  drop?: MaterialDrop
  dragging: boolean
  onClose: () => void
  onSave: (material: Material) => void
  toast: (message: string, isError?: boolean) => void
}

export function MaterialEditor({ material, drop, dragging, onClose, onSave, toast }: MaterialEditorProps) {
  const [fields, setFields] = useState(() => initialFields(material))
  const fieldsRef = useRef(fields)
  const [webRead, setWebRead] = useState<WebRead | null>(() =>
    material?.source_kind === 'web' && materialUrl(material.source_url)
      ? { key: materialUrl(material.source_url), message: '' }
      : null,
  )
  const webReadRef = useRef(webRead)
  const [importing, setImporting] = useState('')
  const [fileMessage, setFileMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const drawer = useRef<HTMLDialogElement>(null)
  const more = useRef<HTMLDetailsElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const mainInput = useRef<HTMLTextAreaElement>(null)
  const webInput = useRef<HTMLTextAreaElement>(null)
  const webTimer = useRef<number | undefined>(undefined)
  const importRequest = useRef<AbortController | null>(null)
  const saveRequest = useRef<AbortController | null>(null)

  useLayoutEffect(() => {
    const dialog = drawer.current
    if (!dialog) return
    dialog.showModal()
    mainInput.current?.focus()
    return () => {
      window.clearTimeout(webTimer.current)
      webReadRef.current?.controller?.abort()
      importRequest.current?.abort()
      importRequest.current = null
      saveRequest.current?.abort()
      dialog.close()
    }
  }, [])

  function updateFields(changes: Partial<EditorFields>) {
    const next = { ...fieldsRef.current, ...changes }
    fieldsRef.current = next
    setFields(next)
  }

  function setField(name: keyof EditorFields, value: string) {
    setError('')
    updateFields({ [name]: value })
  }

  function updateWebRead(read: WebRead | null) {
    webReadRef.current = read
    setWebRead(read)
  }

  function clearWebRead() {
    window.clearTimeout(webTimer.current)
    webReadRef.current?.controller?.abort()
    updateWebRead(null)
  }

  function replaceSource(changes: Partial<EditorFields>) {
    clearWebRead()
    updateFields({
      content: '',
      webExcerpt: '',
      webExcerptEdited: false,
      sourceUrl: '',
      sourceFile: '',
      pageStart: '',
      pageEnd: '',
      fromFile: false,
      ...changes,
    })
    setFileMessage('')
  }

  function fillFile(filename: string, parsed?: ExtractedMaterial, url = '') {
    const paged = parsed?.kind === 'pdf' || parsed?.kind === 'pptx'
    replaceSource({
      content: parsed?.excerpt ?? '',
      sourceUrl: url,
      sourceFile: filename.slice(0, 200),
      pageStart: paged ? String(parsed.page_start) : '',
      pageEnd: paged ? String(parsed.page_end) : '',
      fromFile: true,
    })
    if (parsed) setFileMessage('已读取 ' + filename + (parsed.truncated ? '，保留前 6 万字。' : '。'))
  }

  function showWebResult(url: string, result: WebExcerpt) {
    updateWebRead({
      key: url,
      result,
      message: result.truncated ? '已读取正文，保留前 6 万字。' : '已读取网页正文。',
    })
  }

  function showLinkFailure(url: string, failure: unknown) {
    updateWebRead({
      key: url,
      message: `暂时读不到正文（${errorMessage(failure)}）。可以先保存网址或补充摘录。`,
    })
  }

  function readWeb(url: string): Promise<void> | undefined {
    if (url !== materialUrl(mainLink(fieldsRef.current))) return
    if (webReadRef.current?.key === url) return webReadRef.current.promise

    const controller = new AbortController()
    const promise = fetchMaterial(url, controller.signal)
      .then((result) => {
        if (controller.signal.aborted || webReadRef.current?.controller !== controller) return
        if (result.kind === 'file') {
          if (fieldsRef.current.webExcerptEdited) updateWebRead({ key: url, message: '' })
          else fillFile(result.file.source_file, result.file, url)
          return
        }
        if (!fieldsRef.current.webExcerptEdited) updateFields({ webExcerpt: result.text })
        showWebResult(url, result)
      })
      .catch((failure) => {
        if (controller.signal.aborted || webReadRef.current?.controller !== controller) return
        showLinkFailure(url, failure)
      })
    updateWebRead({ key: url, controller, promise, message: '正在读取链接…' })
    return promise
  }

  function changeContent(content: string) {
    setField('content', content)
    const url = materialUrl(mainLink(fieldsRef.current))
    if (webReadRef.current && webReadRef.current.key !== url) {
      clearWebRead()
      if (!fieldsRef.current.webExcerptEdited) updateFields({ webExcerpt: '' })
    }
    window.clearTimeout(webTimer.current)
    if (url && !webReadRef.current && !fieldsRef.current.webExcerptEdited) {
      webTimer.current = window.setTimeout(() => void readWeb(url), 350)
    }
  }

  async function importMaterial(input: MaterialInput) {
    if (saveRequest.current || importRequest.current) {
      setError('请等当前操作完成，再添加材料。')
      return
    }
    if (input.kind === 'file') {
      const { name, size } = input.file
      if (size > 20 * 1024 * 1024) {
        setError('文件超过 20 MB，请拆分后再选择。')
        return
      }
      if (!fileTypes.split(',').some((extension) => name.toLowerCase().endsWith(extension))) {
        setError('请选择 PDF、DOCX、PPTX、TXT、Markdown 或 CSV 文件。')
        return
      }
    }
    const { content, webExcerpt, sourceUrl, sourceFile, pageStart, pageEnd } = fieldsRef.current
    const replacing = [content, webExcerpt, sourceUrl, sourceFile, pageStart, pageEnd].some((value) => value.trim())
    if (replacing && !window.confirm('替换当前材料的内容和来源？')) return

    const controller = new AbortController()
    importRequest.current = controller
    setError('')
    setImporting(input.kind === 'file' ? '正在读取 ' + input.file.name + '…' : '正在读取链接…')
    try {
      if (input.kind === 'file') {
        const form = new FormData()
        form.append('file', input.file)
        const parsed = await api<ExtractedMaterial>('POST', '/materials/extract', form, controller.signal)
        if (!controller.signal.aborted) {
          setError('')
          fillFile(parsed.source_file, parsed)
        }
        return
      }

      const result = await fetchMaterial(input.url, controller.signal)
      if (controller.signal.aborted) return
      setError('')
      if (result.kind === 'file') fillFile(result.file.source_file, result.file, input.url)
      else {
        replaceSource({ content: input.url, webExcerpt: result.text })
        showWebResult(input.url, result)
      }
    } catch (failure) {
      if (controller.signal.aborted) return
      if (replacing) setError(errorMessage(failure) + ' 已保留原内容。')
      else if (input.kind === 'file') {
        fillFile(input.file.name)
        setError(errorMessage(failure) + ' 可以手动补摘录。')
      } else {
        replaceSource({ content: input.url })
        showLinkFailure(input.url, failure)
      }
    } finally {
      if (importRequest.current === controller) {
        importRequest.current = null
        setImporting('')
      }
    }
  }

  const receiveDrop = useEffectEvent((input: MaterialDrop) => {
    if (input.kind === 'invalid') setError(input.message)
    else void importMaterial(input)
  })
  useEffect(() => {
    if (drop) receiveDrop(drop)
  }, [drop])

  function close() {
    if (saveRequest.current) return
    drawer.current?.close()
    onClose()
  }

  function revealMore() {
    if (more.current) more.current.open = true
  }

  function formError(message: string, field: Element | RadioNodeList | null) {
    setError(message)
    if (field instanceof HTMLElement) {
      if (field.closest('details')) revealMore()
      field.focus()
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (saveRequest.current || importRequest.current) return
    const form = event.currentTarget
    const url = mainLink(fieldsRef.current)
    const draft = materialDraft(fieldsRef.current, material, webReadRef.current?.result)
    const body = draft.body
    if (draft.invalidUrl) {
      formError(
        '请输入有效的 HTTP 或 HTTPS 网址。',
        url ? mainInput.current : form.elements.namedItem('fUrl'),
      )
      return
    }
    if (!body.excerpt && !body.source_url && !body.source_file) {
      formError('粘贴文字或网址，或选择一个文件。', mainInput.current)
      return
    }
    if (body.source_page_start && body.source_page_end && body.source_page_end < body.source_page_start) {
      formError('结束页码不能小于起始页码。', form.elements.namedItem('fPageEnd'))
      return
    }

    window.clearTimeout(webTimer.current)
    const controller = new AbortController()
    saveRequest.current = controller
    setError('')
    setSaving(true)
    try {
      if (url && !fieldsRef.current.webExcerptEdited) await readWeb(materialUrl(url))
      if (controller.signal.aborted) return
      const { body: data } = materialDraft(fieldsRef.current, material, webReadRef.current?.result)
      const revised = material
        ? await api<Material>(
            'PATCH',
            '/materials/' + material.id,
            { ...data, version: material.version },
            controller.signal,
          )
        : await api<Material>('POST', '/materials', data, controller.signal)
      if (controller.signal.aborted) return
      toast(material ? '已更新材料' : '已保存到我的收藏')
      onSave(revised)
      drawer.current?.close()
      onClose()
    } catch (failure) {
      if (!controller.signal.aborted) setError('没能保存材料：' + errorMessage(failure))
    } finally {
      saveRequest.current = null
      if (!controller.signal.aborted) setSaving(false)
    }
  }

  const url = mainLink(fields)
  const normalizedUrl = materialUrl(url)
  const currentWeb = webRead?.key === normalizedUrl ? webRead : null
  const draft = materialDraft(fields, material, currentWeb?.result)
  const body = draft.body
  const sourceLocked = saving || !!importing
  return (
    <dialog
      className="drawer"
      id="drawer"
      ref={drawer}
      aria-labelledby="drawerTitle"
      onCancel={(event) => {
        event.preventDefault()
        close()
      }}
    >
      <div className="drawer-head">
        <h2 style={{ fontSize: 17, fontWeight: 700 }} id="drawerTitle">
          {material ? '编辑材料' : '新增材料'}
        </h2>
        <button
          className="btn quiet sm"
          id="closeMatBtn"
          type="button"
          aria-label="关闭材料编辑"
          disabled={saving}
          onClick={close}
        >
          ✕
        </button>
      </div>
      <form className="material-editor" id="materialEditor" onSubmit={save}>
        <div className="drawer-body">
          <div className="field">
            <div className="row material-field-head" style={{ justifyContent: 'space-between' }}>
              <label htmlFor="fExcerpt">材料内容</label>
              <input
                type="file"
                id="fFile"
                ref={fileInput}
                accept={fileTypes}
                disabled={sourceLocked}
                hidden
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0]
                  event.currentTarget.value = ''
                  if (file) void importMaterial({ kind: 'file', file })
                }}
              />
              <button
                className="btn quiet sm"
                type="button"
                id="pickMatFile"
                disabled={sourceLocked}
                onClick={() => fileInput.current?.click()}
              >
                选择文件
              </button>
            </div>
            <textarea
              className="textarea"
              id="fExcerpt"
              ref={mainInput}
              maxLength={60000}
              placeholder="拖入文件，或粘贴文字或网址"
              aria-describedby="materialFeedback materialStatus"
              disabled={sourceLocked}
              value={fields.content}
              onChange={(event) => changeContent(event.currentTarget.value)}
            />
            <div id="materialFeedback" className="material-feedback" role="status">
              {error || importing || currentWeb?.message || fileMessage}
            </div>
            <div id="materialStatus" className="material-feedback">
              {!importing && !draft.invalidUrl && (body.excerpt || body.source_url || body.source_file) && (
                <MaterialStatus status={body.status} />
              )}
            </div>
            <button
              className="btn quiet sm"
              type="button"
              id="addWebExcerpt"
              hidden={!normalizedUrl}
              disabled={sourceLocked}
              onClick={() => {
                revealMore()
                webInput.current?.focus()
              }}
            >
              {body.excerpt ? '查看或编辑摘录' : '补摘录'}
            </button>
          </div>
          <details className="more" id="moreBox" ref={more}>
            <summary>补充私人备注、标题和来源</summary>
            <div className="field" style={{ marginTop: 12 }}>
              <label htmlFor="fNote">
                私人备注 <span className="hint">供助手选材，分享时会去除</span>
              </label>
              <span className="material-feedback" id="notePrompt">
                {notePrompts.get(body.topic) ?? notePrompts.get(draft.suggestedTopic)}
              </span>
              <textarea
                className="textarea"
                id="fNote"
                maxLength={500}
                aria-describedby="notePrompt"
                value={fields.note}
                disabled={saving}
                onChange={(event) => setField('note', event.currentTarget.value)}
              />
            </div>
            <div className="field" id="webExcerptField" hidden={!url}>
              <label htmlFor="fWebExcerpt">网页摘录</label>
              <textarea
                className="textarea"
                id="fWebExcerpt"
                ref={webInput}
                maxLength={60000}
                placeholder="粘贴希望助手参考的文字"
                value={fields.webExcerpt}
                disabled={sourceLocked}
                onChange={(event) => {
                  setError('')
                  updateFields({ webExcerpt: event.currentTarget.value, webExcerptEdited: true })
                }}
              />
            </div>
            <div className="two">
              <div className="field">
                <label htmlFor="fTitle">标题</label>
                <input
                  className="input"
                  id="fTitle"
                  maxLength={120}
                  placeholder={draft.suggestedTitle || '自动填写'}
                  value={fields.title}
                  disabled={saving}
                  onChange={(event) => setField('title', event.currentTarget.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="fTopic">主题</label>
                <input
                  className="input"
                  id="fTopic"
                  maxLength={40}
                  list="topicList"
                  placeholder={draft.suggestedTopic}
                  value={fields.topic}
                  disabled={saving}
                  onChange={(event) => setField('topic', event.currentTarget.value)}
                />
                <datalist id="topicList">
                  <option>复习</option>
                  <option>出行</option>
                  <option>活动</option>
                  <option>其他</option>
                </datalist>
              </div>
            </div>
            <div className="field" id="sourceUrlField" hidden={!!url}>
              <label htmlFor="fUrl">来源网址</label>
              <input
                className="input"
                id="fUrl"
                name="fUrl"
                inputMode="url"
                maxLength={2000}
                value={fields.sourceUrl}
                disabled={sourceLocked}
                onChange={(event) => setField('sourceUrl', event.currentTarget.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="fSourceFile">来源文件名</label>
              <input
                className="input"
                id="fSourceFile"
                maxLength={200}
                value={fields.sourceFile}
                disabled={sourceLocked}
                onChange={(event) => setField('sourceFile', event.currentTarget.value)}
              />
            </div>
            <div className="two">
              <div className="field">
                <label htmlFor="fPageStart">起始页码</label>
                <input
                  className="input"
                  id="fPageStart"
                  type="number"
                  min={1}
                  max={9999}
                  value={fields.pageStart}
                  disabled={sourceLocked}
                  onChange={(event) => setField('pageStart', event.currentTarget.value)}
                  onInvalid={revealMore}
                />
              </div>
              <div className="field">
                <label htmlFor="fPageEnd">结束页码</label>
                <input
                  className="input"
                  id="fPageEnd"
                  name="fPageEnd"
                  type="number"
                  min={1}
                  max={9999}
                  value={fields.pageEnd}
                  disabled={sourceLocked}
                  onChange={(event) => setField('pageEnd', event.currentTarget.value)}
                  onInvalid={revealMore}
                />
              </div>
            </div>
          </details>
        </div>
        <div className="drawer-foot">
          <button className="btn primary" id="saveMatBtn" type="submit" disabled={sourceLocked}>
            {saving ? '保存中…' : '保存材料'}
          </button>
        </div>
      </form>
      {dragging && <MaterialDropHint />}
    </dialog>
  )
}
