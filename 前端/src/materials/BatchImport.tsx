import { useEffect, useRef, useState } from 'react'
import { api, errorMessage } from '../api'
import type { ExtractedMaterial } from '../types'
import type { Notify } from '../ui/useToast'
import { inferTopic } from './materialDraft'

const TOPICS = ['复习', '出行', '活动', '其他']
const FILE_TYPES = ['.pdf', '.docx', '.pptx', '.md', '.markdown', '.txt', '.csv']
const MAX_BYTES = 20 * 1024 * 1024

type RowState = 'waiting' | 'reading' | 'ready' | 'failed' | 'saved'

interface Row {
  index: number
  file: File
  state: RowState
  title: string
  topic: string
  chars: number
  body: string
  sourceFile: string
  kind: 'courseware' | 'manual'
  error: string
}

function baseName(name: string) {
  return name.replace(/\.[^.]+$/, '')
}

function badReason(file: File): string {
  if (file.size > MAX_BYTES) return '超过 20 MB'
  const lower = file.name.toLowerCase()
  return FILE_TYPES.some((ext) => lower.endsWith(ext)) ? '' : '格式不支持'
}

/** 一次导入多个文件：串行读取正文，读一份存一份，中间可以改标题和主题。 */
export function BatchImport({ files, onClose, onDone, toast }: {
  files: File[]
  onClose: () => void
  onDone: (saved: number) => void
  toast: Notify
}) {
  const [rows, setRows] = useState<Row[]>(() =>
    files.map((file, index) => ({
      index,
      file,
      state: 'waiting' as RowState,
      title: baseName(file.name),
      topic: '其他',
      chars: 0,
      body: '',
      sourceFile: '',
      kind: 'manual' as const,
      error: '',
    })),
  )
  const [sharedTopic, setSharedTopic] = useState('')
  const [saving, setSaving] = useState(false)
  const [progress, setProgress] = useState('')
  const alive = useRef(true)

  useEffect(() => () => {
    alive.current = false
  }, [])

  function update(index: number, patch: Partial<Row>) {
    setRows((prev) => prev.map((row) => (row.index === index ? { ...row, ...patch } : row)))
  }

  // 串行读取：同一时刻只有一个文件在内存里，2 GB 的机器也不会被 PDF 解析撑爆。
  useEffect(() => {
    let cancelled = false
    async function run() {
      for (let i = 0; i < files.length; i++) {
        if (cancelled) return
        const file = files[i]
        const bad = badReason(file)
        if (bad) {
          update(i, { state: 'failed', error: bad })
          continue
        }
        update(i, { state: 'reading' })
        try {
          const form = new FormData()
          form.append('file', file)
          const data = await api<ExtractedMaterial>('POST', '/materials/extract', form)
          if (cancelled) return
          const body = data.excerpt || ''
          const paged = /\.(?:pdf|pptx|docx)$/i.test(file.name)
          update(i, {
            state: 'ready',
            body,
            chars: data.chars || body.length,
            title: (data.suggested_title || baseName(file.name)).slice(0, 120),
            topic: inferTopic([data.suggested_title, file.name, body.slice(0, 12000)].join(' '), paged),
            sourceFile: file.name.slice(0, 200),
            kind: paged ? 'courseware' : 'manual',
          })
        } catch (err) {
          if (!cancelled) update(i, { state: 'failed', error: errorMessage(err) })
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [files])

  function applyTopic(value: string) {
    setSharedTopic(value)
    if (!value) return
    setRows((prev) => prev.map((row) => (row.state === 'ready' ? { ...row, topic: value } : row)))
  }

  const ready = rows.filter((row) => row.state === 'ready')

  async function saveAll() {
    const pending = rows.filter((row) => row.state === 'ready')
    if (!pending.length) {
      toast('还没有读取成功的文件', true)
      return
    }
    setSaving(true)
    let saved = 0
    let firstError = ''
    for (let n = 0; n < pending.length; n++) {
      const row = pending[n]
      if (!alive.current) return
      setProgress('正在保存 ' + (n + 1) + '/' + pending.length + '：' + row.title)
      try {
        await api('POST', '/materials', {
          title: (row.title.trim() || baseName(row.file.name)).slice(0, 120),
          topic: (row.topic.trim() || '其他').slice(0, 40),
          excerpt: row.body,
          source_kind: row.kind,
          source_url: null,
          source_file: row.sourceFile || null,
          note: null,
          status: row.body ? 'citable' : 'unusable',
        })
        saved += 1
        update(row.index, { state: 'saved' })
      } catch (err) {
        if (!firstError) firstError = errorMessage(err)
        update(row.index, { state: 'failed', error: errorMessage(err) })
      }
    }
    setSaving(false)
    setProgress('')
    if (!saved) {
      toast('一条都没存上：' + (firstError || '未知错误'), true)
      return
    }
    toast('已保存 ' + saved + ' 份材料', false)
    onDone(saved)
  }

  return (
    <>
    <div
      className="batch-backdrop"
      aria-hidden="true"
      onClick={() => { if (!saving) onClose() }}
    />
    <div className="batch-drawer" role="dialog" aria-modal="true" aria-label="批量导入材料">
      <div className="drawer-head">
        <h2 style={{ fontSize: 17, fontWeight: 700 }}>批量导入 {files.length} 个文件</h2>
        <button className="btn quiet sm" type="button" aria-label="关闭批量导入" disabled={saving} onClick={onClose}>
          ✕
        </button>
      </div>
      <p className="batch-tip">
        逐个读取正文，读完点「全部保存」。主题已按内容自动推断，也可以在这里统一成同一个。
      </p>
      <div className="batch-topic">
        <label htmlFor="batchSharedTopic">这批统一主题</label>
        <select
          id="batchSharedTopic"
          className="select"
          value={sharedTopic}
          disabled={saving}
          onChange={(event) => applyTopic(event.currentTarget.value)}
        >
          <option value="">不统一，各自推断</option>
          {TOPICS.map((topic) => (
            <option key={topic} value={topic}>{topic}</option>
          ))}
        </select>
      </div>
      <div className="batch-rows">
        {rows.map((row) => {
          const editable = row.state === 'ready'
          return (
            <div className={'batch-row' + (row.state === 'failed' ? ' bad' : '')} key={row.index}>
              <div className="batch-file" title={row.file.name}>{row.file.name}</div>
              <div className="batch-meta">
                {row.state === 'waiting' && <span className="badge">等待读取</span>}
                {row.state === 'reading' && <span className="badge">正在读取…</span>}
                {row.state === 'ready' && <span className="badge ok">{row.chars} 字</span>}
                {row.state === 'saved' && <span className="badge ok">✓ 已保存</span>}
                {row.state === 'failed' && <span className="badge bad">✕ {row.error}</span>}
              </div>
              {row.state === 'ready' || row.state === 'saved' ? (
                <div className="batch-fields">
                  <input
                    className="input"
                    value={row.title}
                    maxLength={120}
                    disabled={!editable || saving}
                    aria-label="标题"
                    onChange={(event) => update(row.index, { title: event.currentTarget.value })}
                  />
                  <select
                    className="select"
                    value={row.topic}
                    disabled={!editable || saving}
                    aria-label="主题"
                    onChange={(event) => update(row.index, { topic: event.currentTarget.value })}
                  >
                    {(TOPICS.includes(row.topic) ? TOPICS : [...TOPICS, row.topic]).map((topic) => (
                      <option key={topic} value={topic}>{topic}</option>
                    ))}
                  </select>
                </div>
              ) : null}
            </div>
          )
        })}
      </div>
      <div className="batch-foot">
        <span className="batch-progress">{saving ? progress : '可保存 ' + ready.length + ' 份'}</span>
        <button className="btn quiet" type="button" disabled={saving} onClick={onClose}>取消</button>
        <button className="btn" type="button" disabled={saving || !ready.length} onClick={() => void saveAll()}>
          {saving ? '保存中…' : '全部保存（' + ready.length + '）'}
        </button>
      </div>
    </div>
    </>
  )
}
