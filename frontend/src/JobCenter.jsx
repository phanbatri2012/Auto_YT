import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const API_BASE = 'http://127.0.0.1:8080'
const ACTIVE_STATUSES = new Set([
  'queued', 'running', 'retry_wait', 'paused'
])
const POLLING_STATUSES = new Set(['queued', 'running', 'retry_wait'])

const STATUS_META = {
  queued: { label: 'Đang chờ', color: '#f39c12' },
  running: { label: 'Đang chạy', color: '#2ecc71' },
  retry_wait: { label: 'Chờ tự phục hồi', color: '#4dd0e1' },
  paused: { label: 'Tạm dừng', color: '#f1c40f' },
  awaiting_review: { label: 'Chưa tự động kiểm tra', color: '#f5b041' },
  review_blocked: { label: 'Tự kiểm tra không đạt', color: '#ff6b6b' },
  done: { label: 'Hoàn thành', color: '#4dd0e1' },
  error: { label: 'Lỗi', color: '#ff5c5c' },
  canceled: { label: 'Đã hủy', color: '#999' }
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('vi-VN')
}

const PIPELINE_LABELS = {
  metadata: 'Metadata',
  chapters: 'Chapter',
  thumbnail_with_text: 'Ảnh có chữ',
  thumbnail_without_text: 'Ảnh không chữ',
  audio: 'Audio'
}

function formatPipeline(pipeline) {
  if (!pipeline) return ''
  const enabledSteps = Object.entries(PIPELINE_LABELS)
    .filter(([key]) => pipeline[key])
    .map(([, label]) => label)
  return enabledSteps.length ? enabledSteps.join(' → ') : 'Không có bước bổ sung'
}

function JobCenter({ onOpenVideo }) {
  const [jobs, setJobs] = useState([])
  const [counts, setCounts] = useState({ total: 0, active: 0, error: 0 })
  const [filter, setFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionId, setActionId] = useState('')
  const requestIdRef = useRef(0)

  useEffect(() => {
    const timeoutId = setTimeout(
      () => setDebouncedSearchQuery(searchQuery.trim()),
      250
    )
    return () => clearTimeout(timeoutId)
  }, [searchQuery])

  const loadJobs = useCallback(async () => {
    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    try {
      const params = new URLSearchParams({
        limit: debouncedSearchQuery ? '500' : '200'
      })
      if (debouncedSearchQuery) params.set('search', debouncedSearchQuery)
      const response = await fetch(`${API_BASE}/api/jobs?${params.toString()}`)
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      if (requestId !== requestIdRef.current) return
      setJobs(Array.isArray(data.items) ? data.items : [])
      setCounts(data.counts || { total: 0, active: 0, error: 0 })
      setError('')
    } catch (loadError) {
      if (requestId !== requestIdRef.current) return
      setError(`Không thể tải danh sách job: ${loadError.message}`)
    } finally {
      if (requestId === requestIdRef.current) setLoading(false)
    }
  }, [debouncedSearchQuery])

  useEffect(() => {
    setLoading(true)
    loadJobs()
  }, [loadJobs])

  const hasPollableJobs = useMemo(
    () => jobs.some(job => POLLING_STATUSES.has(job.status)),
    [jobs]
  )

  useEffect(() => {
    if (!hasPollableJobs) return undefined
    const intervalId = setInterval(loadJobs, 2500)
    return () => clearInterval(intervalId)
  }, [hasPollableJobs, loadJobs])

  const filteredJobs = useMemo(() => jobs.filter(job => {
    if (typeFilter !== 'all' && job.type !== typeFilter) return false
    if (filter === 'active') return ACTIVE_STATUSES.has(job.status)
    if (filter === 'error') return ['error', 'review_blocked'].includes(job.status)
    return true
  }), [filter, jobs, typeFilter])

  const runAction = async (job, action) => {
    setActionId(`${job.id}:${action}`)
    try {
      let endpoint = ''
      if (job.type === 'youtube_download') {
        endpoint = `/api/youtube-download/jobs/${job.raw_id}/${action}`
      } else {
        endpoint = `/api/jobs/${job.raw_id}/${action}`
      }
      const response = await fetch(`${API_BASE}${endpoint}`, { method: 'POST' })
      const data = await response.json()
      if (!response.ok || data.success === false) {
        throw new Error(data.detail || data.error || `HTTP ${response.status}`)
      }
      await loadJobs()
    } catch (actionError) {
      setError(actionError.message)
    } finally {
      setActionId('')
    }
  }

  return (
    <>
      <h1 className="hero-title">Trung tâm Job</h1>
      <p className="hero-subtitle">
        Theo dõi hàng đợi tạo video, audio, ChatGPT và tải YouTube tại một nơi.
      </p>

      <div className="result-panel" style={{ padding: '13px 16px', marginTop: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span aria-hidden="true" style={{ color: '#999' }}>⌕</span>
          <input
            aria-label="Tìm job theo video"
            type="search"
            value={searchQuery}
            onChange={event => setSearchQuery(event.target.value)}
            placeholder="Tìm theo tiêu đề, link YouTube, mã video hoặc mã job..."
            style={{
              flex: 1, minWidth: 0, border: 0, outline: 0,
              background: 'transparent', color: '#fff', fontSize: '0.98em'
            }}
          />
          {searchQuery && (
            <button
              type="button"
              aria-label="Xóa nội dung tìm kiếm"
              className="btn-secondary"
              onClick={() => setSearchQuery('')}
              style={{ width: 'auto', padding: '5px 10px' }}
            >
              ×
            </button>
          )}
        </div>
        <div style={{ color: '#777', fontSize: '0.78em', marginTop: '6px' }}>
          Tìm cả tiêu đề trước/sau khi tạo và từng video trong job tải kênh.
          {searchQuery.trim() && searchQuery.trim() === debouncedSearchQuery
            ? ` · Tìm thấy ${counts.total || 0} job`
            : searchQuery.trim() ? ' · Đang tìm...' : ''}
        </div>
      </div>

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', margin: '14px 0 24px' }}>
        {[
          ['all', `Tất cả ${counts.total || 0}`],
          ['active', `Đang xử lý ${counts.active || 0}`],
          ['error', `Lỗi ${counts.error || 0}`]
        ].map(([value, label]) => (
          <button
            key={value}
            className={filter === value ? 'btn-run' : 'btn-secondary'}
            onClick={() => setFilter(value)}
            style={{ padding: '9px 16px', width: 'auto' }}
          >
            {label}
          </button>
        ))}
        <select
          aria-label="Lọc loại job"
          value={typeFilter}
          onChange={event => setTypeFilter(event.target.value)}
          style={{
            padding: '9px 14px', borderRadius: '7px', border: '1px solid #555',
            background: '#191919', color: '#eee', fontWeight: '600'
          }}
        >
          <option value="all">Mọi loại job</option>
          <option value="video_generation">Tạo video</option>
          <option value="audio_review">Kiểm duyệt audio</option>
          <option value="audio">Tạo audio</option>
          <option value="youtube_download">Tải YouTube</option>
          <option value="chatgpt">Tác vụ ChatGPT</option>
        </select>
        <button
          className="btn-secondary"
          onClick={loadJobs}
          style={{ padding: '9px 16px', width: 'auto', marginLeft: 'auto' }}
        >
          ↻ Làm mới
        </button>
      </div>

      {error && <div style={{ color: '#ff6b6b', marginBottom: '16px' }}>{error}</div>}
      {loading ? (
        <div className="result-panel">Đang tải danh sách job...</div>
      ) : filteredJobs.length === 0 ? (
        <div className="result-panel" style={{ color: '#888' }}>Không có job phù hợp.</div>
      ) : (
        <div style={{ display: 'grid', gap: '12px' }}>
          {filteredJobs.map(job => {
            const status = STATUS_META[job.status] || STATUS_META.error
            return (
              <div key={job.id} className="result-panel" style={{ padding: '18px 20px' }}>
                <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: '260px' }}>
                    <div style={{ display: 'flex', gap: '9px', alignItems: 'center', flexWrap: 'wrap' }}>
                      <strong style={{ color: '#fff' }}>{job.type_label}</strong>
                      <span style={{ color: status.color, border: `1px solid ${status.color}`, borderRadius: '12px', padding: '2px 9px', fontSize: '0.78em' }}>
                        {status.label}
                      </span>
                      {job.queue_position && (
                        <span style={{ color: '#f39c12', fontSize: '0.82em' }}>
                          Vị trí #{job.queue_position}
                        </span>
                      )}
                    </div>
                    <div title={job.title} style={{ color: '#ddd', marginTop: '9px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {job.title || job.id}
                    </div>
                    <div style={{ color: '#999', marginTop: '7px', fontSize: '0.86em' }}>
                      {job.progress || 'Chưa có cập nhật'}
                    </div>
                    {job.type === 'video_generation' && job.pipeline && (
                      <div style={{ color: '#b794f6', marginTop: '6px', fontSize: '0.8em' }}>
                        Pipeline: Kịch bản lõi → {formatPipeline(job.pipeline)}
                      </div>
                    )}
                    <div style={{ color: '#666', marginTop: '6px', fontSize: '0.78em' }}>
                      Tạo: {formatDate(job.created_at)}{job.attempt ? ` · Lần chạy ${job.attempt}` : ''}
                    </div>
                    {job.recovery_count > 0 && (
                      <div style={{ color: '#4dd0e1', marginTop: '6px', fontSize: '0.8em' }}>
                        Tự phục hồi: {job.recovery_count}/{job.recovery_limit || 3}
                        {job.resume_from_step ? ` · Tiếp tục từ ${job.resume_from_step}` : ''}
                      </div>
                    )}
                    {job.status === 'retry_wait' && job.next_retry_at && (
                      <div style={{ color: '#8aa', marginTop: '4px', fontSize: '0.78em' }}>
                        Tự chạy lại lúc {formatDate(job.next_retry_at)}
                      </div>
                    )}
                    {job.error && (
                      <details style={{ marginTop: '9px', color: '#ff6b6b', fontSize: '0.84em' }}>
                        <summary>Xem lỗi</summary>
                        <div style={{ marginTop: '6px', whiteSpace: 'pre-wrap' }}>{job.error}</div>
                      </details>
                    )}
                  </div>

                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    {job.video_id && (
                      <button className="btn-secondary" style={{ padding: '8px 12px' }} onClick={() => onOpenVideo(job.video_id)}>
                        Xem video
                      </button>
                    )}
                    {job.can_pause && (
                      <button className="btn-secondary" disabled={Boolean(actionId)} style={{ padding: '8px 12px' }} onClick={() => runAction(job, 'pause')}>
                        Tạm dừng
                      </button>
                    )}
                    {job.can_resume && (
                      <button className="btn-secondary" disabled={Boolean(actionId)} style={{ padding: '8px 12px' }} onClick={() => runAction(job, 'resume')}>
                        Tiếp tục
                      </button>
                    )}
                    {job.can_cancel && (
                      <button className="btn-secondary" disabled={Boolean(actionId)} style={{ padding: '8px 12px', color: '#ff6b6b' }} onClick={() => runAction(job, job.type === 'youtube_download' ? 'stop' : 'cancel')}>
                        Dừng
                      </button>
                    )}
                    {job.can_retry && (
                      <button className="btn-run" disabled={Boolean(actionId)} style={{ padding: '8px 12px', width: 'auto' }} onClick={() => runAction(job, 'retry')}>
                        Chạy lại
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}

export default JobCenter
