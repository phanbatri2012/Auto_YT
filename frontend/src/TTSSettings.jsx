import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './TTSSettings.css'

const API = 'http://127.0.0.1:8080/api/tts'
const OMNIVOICE_PREVIEW_MAX_CHARACTERS = 200
const OMNIVOICE_PREVIEW_STORAGE_KEY = 'autoyt_omnivoice_preview_id'
const ACTIVE_PREVIEW_STATUSES = new Set(['queued', 'processing'])

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(payload.detail || `HTTP ${response.status}`)
    error.status = response.status
    throw error
  }
  return payload
}

function providerLabel(providerId, providers) {
  return providers.find(provider => provider.id === providerId)?.display_name || providerId
}

function healthLabel(provider) {
  if (!provider.enabled) return 'Đã tắt'
  const health = provider.health || {}
  if (!health.ok) return health.state === 'unconfigured' ? 'Chưa cấu hình' : 'Mất kết nối'
  if (health.busy) return 'Đang bận'
  if (health.state === 'loading') return 'Đang nạp model'
  if (health.model_loaded) return 'Model đã nạp'
  return provider.type === 'local' ? 'Đang nghỉ' : 'Hoạt động'
}

function previewStatusLabel(preview) {
  if (!preview) return ''
  if (preview.status === 'queued') return 'Đang chờ trong hàng đợi GPU'
  if (preview.status === 'processing' && preview.provider_state === 'loading') return 'Đang nạp model OmniVoice'
  if (preview.status === 'processing') return 'Đang tạo audio'
  if (preview.status === 'completed') return 'Hoàn thành'
  if (preview.status === 'canceled') return 'Đã hủy'
  return 'Tạo bản nghe thử thất bại'
}

export default function TTSSettings() {
  const [providers, setProviders] = useState([])
  const [voices, setVoices] = useState([])
  const [defaultVoiceId, setDefaultVoiceId] = useState('')
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [newGenmax, setNewGenmax] = useState({ name: '', provider_voice_id: '' })
  const [sampleFile, setSampleFile] = useState(null)
  const [sampleName, setSampleName] = useState('')
  const [sampleStart, setSampleStart] = useState(0)
  const [sampleEnd, setSampleEnd] = useState(10)
  const [sampleDuration, setSampleDuration] = useState(0)
  const [sampleUrl, setSampleUrl] = useState('')
  const [sampleReferenceText, setSampleReferenceText] = useState('')
  const [previewVoiceId, setPreviewVoiceId] = useState('')
  const [previewText, setPreviewText] = useState('Xin chào, đây là bản nghe thử giọng OmniVoice của hệ thống Auto YT.')
  const [preview, setPreview] = useState(null)
  const credentialRef = useRef(null)
  const previewRef = useRef(null)

  const load = useCallback(async () => {
    const [providerData, voiceData] = await Promise.all([
      api('/providers'),
      api('/voices'),
    ])
    setProviders(providerData.items || [])
    setVoices(voiceData.items || [])
    setDefaultVoiceId(voiceData.default_voice_id || '')
  }, [])

  useEffect(() => {
    load().catch(err => setError(err.message))
  }, [load])

  useEffect(() => () => {
    if (sampleUrl) URL.revokeObjectURL(sampleUrl)
  }, [sampleUrl])

  const activeOmniVoices = useMemo(() => voices.filter(voice =>
    voice.provider_id === 'omnivoice' && voice.status === 'active'
  ), [voices])

  useEffect(() => {
    if (!activeOmniVoices.length) {
      setPreviewVoiceId('')
      return
    }
    if (!activeOmniVoices.some(voice => voice.id === previewVoiceId)) {
      setPreviewVoiceId(activeOmniVoices[0].id)
    }
  }, [activeOmniVoices, previewVoiceId])

  const refreshPreview = useCallback(async previewId => {
    try {
      const result = await api(`/previews/${previewId}`)
      setPreview(result)
      setPreviewVoiceId(result.voice_id)
      setPreviewText(result.text)
      return result
    } catch (err) {
      if (err.status === 404) {
        localStorage.removeItem(OMNIVOICE_PREVIEW_STORAGE_KEY)
        setPreview(null)
        return null
      }
      setError(err.message)
      return null
    }
  }, [])

  useEffect(() => {
    const previewId = localStorage.getItem(OMNIVOICE_PREVIEW_STORAGE_KEY)
    if (previewId) refreshPreview(previewId)
  }, [refreshPreview])

  useEffect(() => {
    if (!preview || !ACTIVE_PREVIEW_STATUSES.has(preview.status)) return undefined
    const timer = setInterval(() => refreshPreview(preview.id), 2000)
    return () => clearInterval(timer)
  }, [preview, refreshPreview])

  const run = async (key, action, successMessage) => {
    setBusy(key)
    setError('')
    setNotice('')
    try {
      const result = await action()
      setNotice(successMessage)
      await load()
      return result
    } catch (err) {
      setError(err.message)
      return null
    } finally {
      setBusy('')
    }
  }

  const visibleVoices = useMemo(() => voices.filter(voice => {
    if (filter === 'archived' && voice.status !== 'archived') return false
    if (filter !== 'all' && filter !== 'archived' && voice.provider_id !== filter) return false
    const query = search.trim().toLocaleLowerCase('vi')
    return !query || voice.name.toLocaleLowerCase('vi').includes(query) ||
      voice.provider_voice_id.toLocaleLowerCase('vi').includes(query)
  }), [filter, search, voices])

  const activeCount = voices.filter(voice => voice.status === 'active').length
  const archivedCount = voices.filter(voice => voice.status === 'archived').length
  const queuedCount = providers.reduce((sum, provider) => sum + (provider.queued_jobs || 0), 0)

  const saveCredential = () => run('credential', async () => {
    const apiKey = credentialRef.current?.value?.trim() || ''
    if (!apiKey) throw new Error('Hãy nhập API key Genmax mới.')
    await api('/providers/genmax/credentials', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: apiKey }),
    })
    credentialRef.current.value = ''
  }, 'Đã thay thế API key Genmax an toàn.')

  const createGenmaxVoice = () => run('new-genmax', async () => {
    await api('/voices', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...newGenmax, provider_id: 'genmax', config: {} }),
    })
    setNewGenmax({ name: '', provider_voice_id: '' })
  }, 'Đã thêm giọng Genmax.')

  const patchVoice = (voice, changes, message) => run(`voice-${voice.id}`, () =>
    api(`/voices/${voice.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    }), message)

  const testVoice = voice => {
    if (voice.provider_id === 'omnivoice') {
      setPreviewVoiceId(voice.id)
      previewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    const isBillable = providers.find(item => item.id === voice.provider_id)
      ?.capabilities?.billable
    if (isBillable && !confirm('Thử giọng Genmax có thể tốn credit. Bạn có muốn tiếp tục?')) return
    run(`test-${voice.id}`, () => api(`/voices/${voice.id}/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm_billable: Boolean(isBillable) }),
    }), 'Đã gửi bản thử giọng. Theo dõi trạng thái tại nhà cung cấp TTS.')
  }

  const chooseSample = event => {
    const file = event.target.files?.[0] || null
    if (sampleUrl) URL.revokeObjectURL(sampleUrl)
    setSampleFile(file)
    setSampleName(file ? file.name.replace(/\.[^.]+$/, '') : '')
    setSampleStart(0)
    setSampleEnd(10)
    setSampleDuration(0)
    setSampleUrl(file ? URL.createObjectURL(file) : '')
  }

  const cloneSample = () => run('clone', async () => {
    if (!sampleFile) throw new Error('Hãy chọn file WAV, MP3, FLAC hoặc OGG.')
    const selectionDuration = Number(sampleEnd) - Number(sampleStart)
    if (selectionDuration < 3 || selectionDuration > 10) {
      throw new Error('Đoạn mẫu phải dài từ 3 đến 10 giây.')
    }
    if (sampleDuration && Number(sampleEnd) > sampleDuration + 0.01) {
      throw new Error('Điểm kết thúc vượt quá thời lượng file.')
    }
    if (sampleReferenceText.trim().length < 2) {
      throw new Error('Hãy nhập chính xác lời nói trong đoạn mẫu đã chọn.')
    }
    const form = new FormData()
    form.append('file', sampleFile)
    form.append('name', sampleName.trim())
    form.append('start_seconds', String(sampleStart))
    form.append('end_seconds', String(sampleEnd))
    form.append('reference_text', sampleReferenceText.trim())
    await api('/providers/omnivoice/clone', { method: 'POST', body: form })
    setSampleFile(null)
    setSampleUrl('')
    setSampleReferenceText('')
  }, 'Đã cắt mẫu, tạo profile và thêm giọng OmniVoice.')

  const createPreview = async () => {
    const text = previewText.trim()
    if (!previewVoiceId) {
      setError('Chưa có giọng OmniVoice hoạt động để nghe thử.')
      return
    }
    if (!text) {
      setError('Hãy nhập nội dung cần nghe thử.')
      return
    }
    if ([...text].length > OMNIVOICE_PREVIEW_MAX_CHARACTERS) {
      setError(`Nội dung nghe thử tối đa ${OMNIVOICE_PREVIEW_MAX_CHARACTERS} ký tự.`)
      return
    }
    setBusy('preview')
    setError('')
    setNotice('')
    try {
      const result = await api('/previews', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice_id: previewVoiceId, text }),
      })
      localStorage.setItem(OMNIVOICE_PREVIEW_STORAGE_KEY, result.id)
      setPreview(result)
      setPreviewText(result.text)
      setNotice('Đã đưa bản nghe thử vào hàng đợi OmniVoice.')
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  const cancelPreview = async () => {
    if (!preview || !ACTIVE_PREVIEW_STATUSES.has(preview.status)) return
    setBusy('preview-cancel')
    setError('')
    try {
      const result = await api(`/previews/${preview.id}/cancel`, { method: 'POST' })
      setPreview(result)
      setNotice('Đã gửi yêu cầu hủy bản nghe thử.')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="tts-page">
      <div className="tts-heading">
        <div>
          <h1>Giọng đọc &amp; TTS</h1>
          <p>Quản lý tập trung giọng cloud, local và engine mở rộng.</p>
        </div>
        <button className="tts-button" onClick={() => run('refresh', async () => {}, 'Đã làm mới trạng thái.')}>↻ Làm mới</button>
      </div>

      <div className="tts-summary">
        <span>🎙️ Hoạt động: <b>{activeCount}</b></span>
        <span>📦 Lưu trữ: <b>{archivedCount}</b></span>
        <span>⏳ Job đang chờ: <b>{queuedCount}</b></span>
        <label>
          Giọng mặc định chung
          <select value={defaultVoiceId} onChange={event => {
            const voiceId = event.target.value
            run('default', () => api('/voices/default', {
              method: 'PUT', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ voice_id: voiceId }),
            }), 'Đã đổi giọng mặc định chung.')
          }}>
            {voices.filter(voice => voice.status === 'active').map(voice => (
              <option key={voice.id} value={voice.id}>
                {providerLabel(voice.provider_id, providers)} · {voice.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="tts-provider-grid">
        {providers.map(provider => (
          <article className={`tts-card ${provider.health?.ok ? 'healthy' : 'offline'}`} key={provider.id}>
            <div className="tts-card-title">
              <div>
                <h2>{provider.display_name}</h2>
                <span className="tts-provider-kind">{provider.type === 'local' ? 'Local · dùng GPU' : 'Cloud · có credit'}</span>
              </div>
              <span className="tts-health">{healthLabel(provider)}</span>
            </div>
            {provider.health?.error && <small className="tts-error-inline">{provider.health.error}</small>}
            <div className="tts-actions">
              <button className="tts-button" disabled={busy === `provider-${provider.id}`} onClick={() =>
                run(`provider-${provider.id}`, () => api(`/providers/${provider.id}/test`, { method: 'POST' }), `Kết nối ${provider.display_name} hoạt động.`)
              }>Kiểm tra kết nối</button>
              <label className="tts-toggle">
                <input type="checkbox" checked={provider.enabled} onChange={event => {
                  const enabled = event.target.checked
                  run(`provider-${provider.id}`, () => api(`/providers/${provider.id}`, {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled }),
                  }), enabled ? `Đã bật ${provider.display_name}.` : `Đã tắt ${provider.display_name}.`)
                }} /> Bật provider
              </label>
            </div>
            {provider.id === 'genmax' && (
              <div className="tts-provider-config">
                <label>API key <input ref={credentialRef} type="password" autoComplete="new-password" placeholder={provider.health?.configured ? '•••••••• (đã cấu hình)' : 'Nhập API key mới'} /></label>
                <button className="tts-button primary" onClick={saveCredential}>Thay thế key</button>
                <button className="tts-button danger" onClick={() => {
                  if (confirm('Xóa API key Genmax đã lưu?')) run('credential-delete', () => api('/providers/genmax/credentials', { method: 'DELETE' }), 'Đã xóa API key Genmax.')
                }}>Xóa key</button>
              </div>
            )}
            {provider.id === 'omnivoice' && (
              <button className="tts-button" onClick={() => run('sync', () => api('/providers/omnivoice/sync', { method: 'POST' }), 'Đã đồng bộ profile OmniVoice; không tạo bản ghi trùng.')}>Đồng bộ profile .pt</button>
            )}
          </article>
        ))}
      </div>

      <section className="tts-card tts-preview" ref={previewRef}>
        <div className="tts-card-title">
          <div>
            <h2>Nghe thử OmniVoice</h2>
            <span className="tts-provider-kind">Nhập nội dung ngắn và nghe trực tiếp, không gắn vào video.</span>
          </div>
          {preview && <span className={`tts-preview-status ${preview.status}`}>{previewStatusLabel(preview)}</span>}
        </div>
        {activeOmniVoices.length ? (
          <>
            <label className="tts-preview-voice">
              Giọng OmniVoice
              <select
                value={previewVoiceId}
                disabled={preview && ACTIVE_PREVIEW_STATUSES.has(preview.status)}
                onChange={event => setPreviewVoiceId(event.target.value)}
              >
                {activeOmniVoices.map(voice => (
                  <option key={voice.id} value={voice.id}>{voice.name}</option>
                ))}
              </select>
            </label>
            <label className="tts-preview-text">
              Nội dung nghe thử
              <textarea
                rows="4"
                maxLength={OMNIVOICE_PREVIEW_MAX_CHARACTERS}
                value={previewText}
                disabled={preview && ACTIVE_PREVIEW_STATUSES.has(preview.status)}
                onChange={event => setPreviewText(event.target.value)}
                placeholder="Nhập tối đa 200 ký tự..."
              />
              <small>{[...previewText].length}/{OMNIVOICE_PREVIEW_MAX_CHARACTERS} ký tự</small>
            </label>
            <div className="tts-actions">
              <button
                className="tts-button primary"
                disabled={busy === 'preview' || (preview && ACTIVE_PREVIEW_STATUSES.has(preview.status))}
                onClick={createPreview}
              >
                {preview ? 'Tạo lại bản nghe thử' : 'Tạo bản nghe thử'}
              </button>
              {preview && ACTIVE_PREVIEW_STATUSES.has(preview.status) && (
                <button className="tts-button danger" disabled={busy === 'preview-cancel'} onClick={cancelPreview}>Hủy</button>
              )}
            </div>
            {preview?.status === 'completed' && preview.audio_url && (
              <div className="tts-preview-result">
                <audio key={preview.audio_url} controls preload="metadata" src={preview.audio_url} />
                <a className="tts-button" href={`${preview.audio_url}?download=1`}>Tải WAV</a>
                {preview.duration_seconds && <small>Thời lượng: {preview.duration_seconds.toFixed(1)} giây</small>}
              </div>
            )}
            {preview?.status === 'failed' && <div className="tts-error-inline">{preview.error || 'OmniVoice không tạo được bản nghe thử.'}</div>}
          </>
        ) : (
          <p className="tts-empty">Chưa có giọng OmniVoice hoạt động. Hãy tạo hoặc đồng bộ profile trước.</p>
        )}
      </section>

      <section className="tts-card tts-create-grid">
        <div>
          <h2>Thêm giọng Genmax</h2>
          <input value={newGenmax.name} onChange={event => setNewGenmax(value => ({ ...value, name: event.target.value }))} placeholder="Tên hiển thị" />
          <input value={newGenmax.provider_voice_id} onChange={event => setNewGenmax(value => ({ ...value, provider_voice_id: event.target.value }))} placeholder="Voice ID Genmax" />
          <button className="tts-button primary" disabled={busy === 'new-genmax'} onClick={createGenmaxVoice}>＋ Thêm giọng</button>
        </div>
        <div>
          <h2>Tạo profile OmniVoice</h2>
          <input type="file" accept="audio/wav,audio/mpeg,audio/flac,audio/ogg,.wav,.mp3,.flac,.ogg" onChange={chooseSample} />
          {sampleUrl && <audio controls src={sampleUrl} onLoadedMetadata={event => {
            const duration = event.currentTarget.duration
            if (Number.isFinite(duration)) {
              setSampleDuration(duration)
              setSampleEnd(Math.min(10, duration))
            }
          }} />}
          {sampleFile && sampleDuration > 10 && <small>File dài {sampleDuration.toFixed(1)} giây: bắt buộc chọn đoạn sạch 3–10 giây.</small>}
          <input value={sampleName} onChange={event => setSampleName(event.target.value)} placeholder="Tên giọng" />
          <textarea value={sampleReferenceText} onChange={event => setSampleReferenceText(event.target.value)} placeholder="Nhập chính xác lời nói trong đoạn mẫu 3–10 giây" rows="3" />
          <div className="tts-time-range">
            <label>Bắt đầu (giây)<input type="number" min="0" step="0.1" value={sampleStart} onChange={event => setSampleStart(event.target.value)} /></label>
            <label>Kết thúc (giây)<input type="number" min="3" step="0.1" value={sampleEnd} onChange={event => setSampleEnd(event.target.value)} /></label>
          </div>
          <button className="tts-button primary" disabled={busy === 'clone'} onClick={cloneSample}>Tạo profile</button>
        </div>
      </section>

      <section className="tts-card">
        <div className="tts-list-tools">
          <h2>Danh mục giọng</h2>
          <input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Tìm tên hoặc Voice ID..." />
          <select value={filter} onChange={event => setFilter(event.target.value)}>
            <option value="all">Tất cả</option>
            {providers.map(provider => <option key={provider.id} value={provider.id}>{provider.display_name}</option>)}
            <option value="archived">Đã lưu trữ</option>
          </select>
        </div>
        <div className="tts-voice-list">
          {visibleVoices.map(voice => (
            <div className="tts-voice" key={voice.id}>
              <div>
                <strong>{voice.name}</strong>
                <span className={`tts-provider-badge ${voice.provider_id}`}>{providerLabel(voice.provider_id, providers)}</span>
                <small>
                  {voice.provider_voice_id} · revision {voice.revision} · {voice.status}
                  {voice.provider_id === 'omnivoice' && ` · ${voice.config?.target_words_per_minute || 145} từ/phút`}
                </small>
              </div>
              <div className="tts-actions">
                {voice.status === 'active' && <button className="tts-button" onClick={() => testVoice(voice)}>Thử giọng</button>}
                {voice.provider_id === 'omnivoice' && <button className="tts-button" onClick={() => {
                  const current = voice.config?.target_words_per_minute || 145
                  const raw = prompt('Tốc độ đọc mục tiêu (105–240 từ/phút):', String(current))
                  if (raw === null) return
                  const target = Number(raw)
                  if (!Number.isFinite(target) || target < 105 || target > 240) {
                    setError('Tốc độ OmniVoice phải từ 105 đến 240 từ/phút.')
                    return
                  }
                  patchVoice(voice, {
                    config: { ...(voice.config || {}), target_words_per_minute: target },
                  }, 'Đã lưu tốc độ OmniVoice; job mới sẽ dùng cấu hình này.')
                }}>Tốc độ</button>}
                <button className="tts-button" onClick={() => {
                  const name = prompt('Tên hiển thị mới:', voice.name)
                  if (name?.trim()) patchVoice(voice, { name: name.trim() }, 'Đã đổi tên giọng; video cũ vẫn giữ snapshot.')
                }}>Đổi tên</button>
                {voice.status === 'archived' ? (
                  <button className="tts-button primary" onClick={() => patchVoice(voice, { status: 'active' }, 'Đã khôi phục giọng.')}>Khôi phục</button>
                ) : (
                  <button className="tts-button danger" onClick={() => {
                    if (confirm('Lưu trữ giọng này? Video cũ vẫn hiển thị và phát đúng.')) patchVoice(voice, { status: 'archived' }, 'Đã lưu trữ giọng.')
                  }}>Lưu trữ</button>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>

      {busy && <div className="tts-status">Đang xử lý…</div>}
      {notice && <div className="tts-status success">{notice}</div>}
      {error && <div className="tts-status error">{error}</div>}
    </div>
  )
}
