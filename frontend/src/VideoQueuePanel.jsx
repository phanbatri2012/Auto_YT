import { useCallback, useEffect, useMemo, useState } from 'react'

const API_BASE = 'http://127.0.0.1:8080'
const ACTIVE_STATUSES = ['queued', 'running', 'retry_wait', 'paused']
const POLLING_STATUSES = ['queued', 'running', 'retry_wait']

function VideoQueuePanel({ refreshKey, onOpenJobCenter, onOpenVideo }) {
  const [jobs, setJobs] = useState([])

  const loadQueue = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/jobs?job_type=video_generation&limit=30`)
      const data = await response.json()
      if (response.ok) setJobs(Array.isArray(data.items) ? data.items : [])
    } catch {
      // Job Center exposes connection errors; keep this compact panel quiet.
    }
  }, [])

  useEffect(() => {
    loadQueue()
  }, [loadQueue, refreshKey])

  const hasPollableJobs = useMemo(
    () => jobs.some(job => POLLING_STATUSES.includes(job.status)),
    [jobs]
  )

  useEffect(() => {
    if (!hasPollableJobs) return undefined
    const intervalId = setInterval(loadQueue, 2500)
    return () => clearInterval(intervalId)
  }, [hasPollableJobs, loadQueue])

  const visibleJobs = useMemo(() => {
    const active = jobs
      .filter(job => ACTIVE_STATUSES.includes(job.status))
      .sort((left, right) => {
        if (left.status === 'running' && right.status !== 'running') return -1
        if (right.status === 'running' && left.status !== 'running') return 1
        return (left.queue_position || 0) - (right.queue_position || 0)
      })
    const recent = jobs.filter(job => !ACTIVE_STATUSES.includes(job.status)).slice(0, 3)
    return [...active, ...recent]
  }, [jobs])

  const cancelJob = async jobId => {
    await fetch(`${API_BASE}/api/jobs/${jobId}/cancel`, { method: 'POST' })
    await loadQueue()
  }

  if (visibleJobs.length === 0) return null

  return (
    <div className="result-panel" style={{ marginTop: '20px', padding: '18px 22px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'center' }}>
        <strong style={{ color: 'var(--accent)' }}>Hàng đợi tạo video</strong>
        <button className="btn-secondary" style={{ padding: '6px 10px' }} onClick={onOpenJobCenter}>
          Mở Trung tâm Job
        </button>
      </div>
      <div style={{ display: 'grid', gap: '8px', marginTop: '12px' }}>
        {visibleJobs.map(job => (
          <div key={job.id} style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '9px 11px', background: '#171717', borderRadius: '7px' }}>
            <span>{job.status === 'running' ? '▶' : job.status === 'retry_wait' ? '↻' : job.status === 'paused' ? '⏸' : job.status === 'queued' ? '⏳' : job.status === 'done' ? '✅' : '⚠️'}</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div title={job.title} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: '#ddd', fontSize: '0.9em' }}>
                {job.title}
              </div>
              <div style={{ color: '#888', fontSize: '0.78em', marginTop: '3px' }}>
                {job.queue_position ? `Vị trí #${job.queue_position} · ` : ''}{job.progress}
              </div>
            </div>
            {job.video_id && job.status === 'done' && (
              <button className="btn-secondary" style={{ padding: '5px 9px' }} onClick={() => onOpenVideo(job.video_id)}>Xem</button>
            )}
            {ACTIVE_STATUSES.includes(job.status) && (
              <button className="btn-secondary" style={{ padding: '5px 9px', color: '#ff6b6b' }} onClick={() => cancelJob(job.raw_id)}>Dừng</button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export default VideoQueuePanel
