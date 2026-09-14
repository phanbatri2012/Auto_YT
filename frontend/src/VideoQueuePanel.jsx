import { useCallback, useEffect, useMemo, useState } from 'react'

const API_BASE = 'http://127.0.0.1:8080'
const ACTIVE_STATUSES = ['queued', 'running', 'retry_wait', 'paused']
const POLLING_STATUSES = ['queued', 'running', 'retry_wait']

function VideoQueuePanel({ refreshKey, onOpenJobCenter, onOpenVideo }) {
  const [jobs, setJobs] = useState([])
  const [promptVersions, setPromptVersions] = useState([])
  const [voices, setVoices] = useState([])
  const [editor, setEditor] = useState(null)
  const [actionId, setActionId] = useState('')
  const [actionError, setActionError] = useState('')

  const loadQueue = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/jobs?job_type=video_generation&limit=500`)
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
    await runJobAction(jobId, 'cancel')
  }

  const loadEditorOptions = async () => {
    const [promptResponse, voiceResponse] = await Promise.all([
      fetch(`${API_BASE}/api/prompts`),
      fetch(`${API_BASE}/api/voices`)
    ])
    const promptData = await promptResponse.json()
    const voiceData = await voiceResponse.json()
    if (!promptResponse.ok || !promptData.versions) {
      throw new Error(promptData.detail || 'Không thể tải danh sách bộ prompt.')
    }
    if (!voiceResponse.ok || !Array.isArray(voiceData.voices)) {
      throw new Error(voiceData.detail || 'Không thể tải danh sách giọng đọc.')
    }
    const versions = Object.entries(promptData.versions).map(([id, version]) => ({
      id,
      name: version.name || id,
      defaultVoiceId: version.default_voice_id || ''
    }))
    setPromptVersions(versions)
    setVoices(voiceData.voices)
    return { versions, voices: voiceData.voices }
  }

  const beginEdit = async job => {
    setActionError('')
    setActionId(`${job.raw_id}:edit`)
    try {
      const options = await loadEditorOptions()
      const promptVersion = job.prompt_version || options.versions[0]?.id || ''
      const fallbackVoiceId = options.versions.find(
        version => version.id === promptVersion
      )?.defaultVoiceId || options.voices[0]?.id || ''
      setEditor({
        jobId: job.raw_id,
        url: job.video_url || '',
        promptVersion,
        voiceId: job.voice_id || fallbackVoiceId
      })
    } catch (error) {
      setActionError(error.message)
    } finally {
      setActionId('')
    }
  }

  const saveJob = async () => {
    if (!editor?.url.trim()) {
      setActionError('Link YouTube không được để trống.')
      return
    }
    setActionId(`${editor.jobId}:save`)
    setActionError('')
    try {
      const response = await fetch(`${API_BASE}/api/jobs/${editor.jobId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: editor.url.trim(),
          prompt_version: editor.promptVersion,
          voice_id: editor.voiceId
        })
      })
      const data = await response.json()
      if (!response.ok || data.success === false) {
        throw new Error(data.detail || 'Không thể cập nhật job.')
      }
      setEditor(null)
      await loadQueue()
    } catch (error) {
      setActionError(error.message)
    } finally {
      setActionId('')
    }
  }

  const deleteJob = async job => {
    const confirmed = window.confirm(
      'Xóa job này khỏi hàng đợi/lịch sử? Video đã lưu trong Dashboard sẽ được giữ nguyên.'
    )
    if (!confirmed) return
    setActionId(`${job.raw_id}:delete`)
    setActionError('')
    try {
      const response = await fetch(`${API_BASE}/api/jobs/${job.raw_id}`, {
        method: 'DELETE'
      })
      const data = await response.json()
      if (!response.ok || data.success === false) {
        throw new Error(data.detail || 'Không thể xóa job.')
      }
      if (editor?.jobId === job.raw_id) setEditor(null)
      await loadQueue()
    } catch (error) {
      setActionError(error.message)
    } finally {
      setActionId('')
    }
  }

  const runJobAction = async (jobId, action) => {
    setActionId(`${jobId}:${action}`)
    setActionError('')
    try {
      const response = await fetch(`${API_BASE}/api/jobs/${jobId}/${action}`, {
        method: 'POST'
      })
      const data = await response.json()
      if (!response.ok || data.success === false) {
        throw new Error(data.detail || `Không thể ${action} job.`)
      }
      await loadQueue()
    } catch (error) {
      setActionError(error.message)
    } finally {
      setActionId('')
    }
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
      {actionError && (
        <div style={{ color: '#ff6b6b', marginTop: '10px', fontSize: '0.84em' }}>
          {actionError}
        </div>
      )}
      <div style={{ display: 'grid', gap: '8px', marginTop: '12px' }}>
        {visibleJobs.map(job => (
          <div key={job.id} style={{ padding: '9px 11px', background: '#171717', borderRadius: '7px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <span>{job.status === 'running' ? '▶' : job.status === 'retry_wait' ? '↻' : job.status === 'paused' ? '⏸' : job.status === 'queued' ? '⏳' : job.status === 'done' ? '✅' : '⚠️'}</span>
              <div style={{ minWidth: '180px', flex: 1 }}>
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
              {job.can_edit && (
                <button className="btn-secondary" disabled={Boolean(actionId)} style={{ padding: '5px 9px' }} onClick={() => beginEdit(job)}>Sửa</button>
              )}
              {job.can_retry && (
                <button className="btn-secondary" disabled={Boolean(actionId)} style={{ padding: '5px 9px', color: '#4dd0e1' }} onClick={() => runJobAction(job.raw_id, 'retry')}>Chạy lại</button>
              )}
              {ACTIVE_STATUSES.includes(job.status) && (
                <button className="btn-secondary" disabled={Boolean(actionId)} style={{ padding: '5px 9px', color: '#ffb347' }} onClick={() => cancelJob(job.raw_id)}>Dừng</button>
              )}
              {job.can_delete && (
                <button className="btn-secondary" disabled={Boolean(actionId)} style={{ padding: '5px 9px', color: '#ff6b6b' }} onClick={() => deleteJob(job)}>Xóa</button>
              )}
            </div>
            {editor?.jobId === job.raw_id && (
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 2fr) minmax(150px, 1fr) minmax(150px, 1fr) auto auto', gap: '8px', marginTop: '10px' }}>
                <input
                  aria-label="Link YouTube của job"
                  value={editor.url}
                  onChange={event => setEditor({ ...editor, url: event.target.value })}
                  style={{ minWidth: 0 }}
                />
                <select
                  aria-label="Bộ prompt của job"
                  value={editor.promptVersion}
                  onChange={event => {
                    const promptVersion = event.target.value
                    const defaultVoiceId = promptVersions.find(
                      version => version.id === promptVersion
                    )?.defaultVoiceId
                    setEditor({
                      ...editor,
                      promptVersion,
                      voiceId: defaultVoiceId || editor.voiceId
                    })
                  }}
                >
                  {promptVersions.map(version => (
                    <option key={version.id} value={version.id}>{version.name}</option>
                  ))}
                </select>
                <select
                  aria-label="Giọng đọc của job"
                  value={editor.voiceId}
                  onChange={event => setEditor({ ...editor, voiceId: event.target.value })}
                >
                  {voices.map(voice => (
                    <option key={voice.id} value={voice.id}>{voice.name}</option>
                  ))}
                </select>
                <button className="btn-run" disabled={Boolean(actionId)} style={{ width: 'auto', padding: '6px 11px' }} onClick={saveJob}>Lưu</button>
                <button className="btn-secondary" disabled={Boolean(actionId)} style={{ padding: '6px 11px' }} onClick={() => setEditor(null)}>Hủy</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export default VideoQueuePanel
