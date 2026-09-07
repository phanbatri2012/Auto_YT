import { useEffect, useMemo, useRef, useState } from 'react'


const ACTIVE_JOB_STATUSES = new Set(['running', 'paused', 'stopping'])
const TERMINAL_JOB_STATUSES = new Set(['completed', 'completed_with_errors', 'stopped'])

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return 'Không rõ thời lượng'
  const total = Math.floor(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const remaining = total % 60
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`
    : `${minutes}:${String(remaining).padStart(2, '0')}`
}

function formatProgress(value) {
  const progress = Math.min(100, Math.max(0, Number(value) || 0))
  return Number.isInteger(progress) ? String(progress) : progress.toFixed(1)
}


function YouTubeDownloader() {
  const [url, setUrl] = useState('')
  const [videos, setVideos] = useState([])
  const [selectedIds, setSelectedIds] = useState([])
  const [isLoading, setIsLoading] = useState(false)
  const [isChoosingFolder, setIsChoosingFolder] = useState(false)
  const [error, setError] = useState('')
  const [destination, setDestination] = useState('')
  const [downloadJob, setDownloadJob] = useState(null)
  const [sourceIsChannel, setSourceIsChannel] = useState(false)
  const pollTimerRef = useRef(null)

  useEffect(() => () => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current)
  }, [])

  const selectedVideos = useMemo(
    () => videos.filter(video => selectedIds.includes(video.id)),
    [videos, selectedIds]
  )
  const allSelected = videos.length > 0 && selectedIds.length === videos.length
  const downloadActive = ACTIVE_JOB_STATUSES.has(downloadJob?.status)

  const loadVideos = async () => {
    if (!url.trim() || isLoading || downloadActive) return
    setIsLoading(true)
    setError('')
    setVideos([])
    setSelectedIds([])
    setDownloadJob(null)
    setDestination('')
    setSourceIsChannel(false)
    try {
      const response = await fetch('http://127.0.0.1:8080/api/youtube-download/list', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim() })
      })
      const data = await response.json()
      if (!response.ok || !data.success) {
        throw new Error(data.detail || 'Không thể đọc link YouTube.')
      }
      setVideos(data.videos || [])
      setSourceIsChannel(Boolean(data.is_channel))
      if ((data.videos || []).length === 1) {
        setSelectedIds([data.videos[0].id])
      }
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setIsLoading(false)
    }
  }

  const toggleVideo = videoId => {
    setSelectedIds(current => (
      current.includes(videoId)
        ? current.filter(id => id !== videoId)
        : [...current, videoId]
    ))
  }

  const toggleAll = () => {
    setSelectedIds(allSelected ? [] : videos.map(video => video.id))
  }

  const pollJob = jobId => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    const fetchJob = async () => {
      try {
        const response = await fetch(
          `http://127.0.0.1:8080/api/youtube-download/jobs/${jobId}`
        )
        const data = await response.json()
        if (!response.ok || !data.success) {
          throw new Error(data.detail || 'Không thể đọc tiến độ tải.')
        }
        setDownloadJob(data.job)
        if (TERMINAL_JOB_STATUSES.has(data.job.status)) {
          clearInterval(pollTimerRef.current)
          pollTimerRef.current = null
        }
      } catch (pollError) {
        setError(pollError.message)
      }
    }
    pollTimerRef.current = setInterval(fetchJob, 1000)
    fetchJob()
  }

  const startDownload = async () => {
    if (!selectedVideos.length || downloadActive || isChoosingFolder) return
    setIsChoosingFolder(true)
    setError('')
    try {
      const folderResponse = await fetch(
        'http://127.0.0.1:8080/api/youtube-download/select-folder',
        { method: 'POST' }
      )
      const folderData = await folderResponse.json()
      if (!folderResponse.ok || !folderData.success) {
        throw new Error(folderData.detail || 'Không thể chọn thư mục lưu.')
      }
      if (folderData.cancelled || !folderData.path) return
      setDestination(folderData.path)

      const response = await fetch('http://127.0.0.1:8080/api/youtube-download/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          destination: folderData.path,
          number_folders: sourceIsChannel,
          videos: selectedVideos.map(({ id, title, url: videoUrl, position }) => ({
            id,
            title,
            url: videoUrl,
            position
          }))
        })
      })
      const data = await response.json()
      if (!response.ok || !data.success) {
        throw new Error(data.detail || 'Không thể bắt đầu tải video.')
      }
      setDownloadJob({
        id: data.job_id,
        status: 'running',
        total: selectedVideos.length,
        completed: 0,
        failed: 0,
        progress: 0,
        items: selectedVideos.map(video => ({
          ...video,
          status: 'pending',
          progress: 0,
          phase: 'Đang chờ'
        }))
      })
      pollJob(data.job_id)
    } catch (downloadError) {
      setError(downloadError.message)
    } finally {
      setIsChoosingFolder(false)
    }
  }

  const controlDownload = async action => {
    if (!downloadJob?.id) return
    if (action === 'stop' && !window.confirm('Dừng toàn bộ job tải hiện tại?')) return
    setError('')
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/youtube-download/jobs/${downloadJob.id}/${action}`,
        { method: 'POST' }
      )
      const data = await response.json()
      if (!response.ok || !data.success) {
        throw new Error(data.detail || 'Không thể điều khiển job tải.')
      }
      setDownloadJob(data.job)
      if (action === 'resume' && !pollTimerRef.current) {
        pollJob(downloadJob.id)
      }
    } catch (controlError) {
      setError(controlError.message)
    }
  }

  const jobItemsById = new Map(
    (downloadJob?.items || []).map(item => [item.id, item])
  )

  return (
    <div className="youtube-downloader">
      <h1 className="hero-title">YouTube Downloader</h1>
      <p className="hero-subtitle">
        Tải video, mô tả và transcript vào thư mục riêng cho từng video.
      </p>

      <div className="input-group downloader-input-group">
        <input
          className="hero-input"
          value={url}
          onChange={event => setUrl(event.target.value)}
          onKeyDown={event => event.key === 'Enter' && loadVideos()}
          placeholder="Dán link video hoặc link kênh YouTube..."
          disabled={downloadActive}
        />
        <button
          className="btn-run"
          onClick={loadVideos}
          disabled={!url.trim() || isLoading || downloadActive}
        >
          {isLoading ? '⏳ Đang tải danh sách...' : 'Load'}
        </button>
      </div>

      {error && <div className="downloader-error">{error}</div>}

      {videos.length > 0 && (
        <div className="result-panel downloader-panel">
          <div className="downloader-toolbar">
            <label className="downloader-select-all">
              <input type="checkbox" checked={allSelected} onChange={toggleAll} />
              Chọn tất cả ({videos.length})
            </label>
            <span>Đã chọn: <strong>{selectedVideos.length}</strong></span>
            <button
              className="btn-run downloader-download-button"
              onClick={startDownload}
              disabled={!selectedVideos.length || downloadActive || isChoosingFolder}
            >
              {isChoosingFolder
                ? '📁 Đang mở cửa sổ chọn thư mục...'
                : downloadActive
                  ? '⬇️ Đang tải...'
                  : `⬇️ Tải ${selectedVideos.length || ''} video đã chọn`}
            </button>
          </div>

          {destination && (
            <div className="downloader-destination">📁 Lưu tại: {destination}</div>
          )}

          {downloadJob && (
            <div className="downloader-summary">
              <div>
                Tiến độ tổng: <strong>{formatProgress(downloadJob.progress)}%</strong>
                {' · '}Hoàn thành: {downloadJob.completed || 0}/{downloadJob.total || 0}
                {downloadJob.failed > 0 && ` · Lỗi: ${downloadJob.failed}`}
                {downloadJob.status === 'paused' && ' · ⏸️ Đang tạm dừng'}
                {downloadJob.status === 'stopping' && ' · ⏳ Đang dừng'}
                {downloadJob.status === 'stopped' && ' · ⏹️ Đã dừng'}
                {downloadJob.status === 'completed' && ' · ✅ Đã tải xong'}
                {downloadJob.status === 'completed_with_errors' && ' · ⚠️ Đã kết thúc với lỗi'}
              </div>
              <div className="downloader-progress-track" aria-hidden="true">
                <span style={{ width: `${Math.min(100, Number(downloadJob.progress) || 0)}%` }} />
              </div>
            </div>
          )}

          {downloadActive && (
            <div className="downloader-controls">
              {downloadJob.status === 'running' && (
                <button onClick={() => controlDownload('pause')}>⏸️ Tạm dừng</button>
              )}
              {downloadJob.status === 'paused' && (
                <button onClick={() => controlDownload('resume')}>▶️ Tiếp tục</button>
              )}
              {downloadJob.status !== 'stopping' && (
                <button className="danger" onClick={() => controlDownload('stop')}>
                  ⏹️ Dừng
                </button>
              )}
            </div>
          )}

          <div className="downloader-list">
            {videos.map(video => {
              const item = jobItemsById.get(video.id)
              return (
                <label key={video.id} className="downloader-video-card">
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(video.id)}
                    onChange={() => toggleVideo(video.id)}
                    disabled={downloadActive}
                  />
                  <div className="downloader-thumbnail">
                    {video.thumbnail
                      ? <img src={video.thumbnail} alt="" />
                      : <span>▶</span>}
                  </div>
                  <div className="downloader-video-info">
                    <strong title={video.title}>{video.title}</strong>
                    <span>
                      {video.channel || 'YouTube'} · {formatDuration(video.duration)} · {video.id}
                    </span>
                    {item && item.status !== 'pending' && (
                      <div className={`downloader-item-status ${item.status}`}>
                        {item.status === 'downloading' && (
                          <>
                            <span>
                              {item.phase || 'Đang tải'}: {formatProgress(item.progress)}%
                            </span>
                            <div className="downloader-progress-track item" aria-hidden="true">
                              <span style={{ width: `${Math.min(100, Number(item.progress) || 0)}%` }} />
                            </div>
                          </>
                        )}
                        {item.status === 'completed' && '✅ Hoàn thành'}
                        {item.status === 'failed' && `❌ ${item.error}`}
                        {item.status === 'stopped' && '⏹️ Đã dừng'}
                        {item.folder && <small>{item.folder}</small>}
                      </div>
                    )}
                  </div>
                </label>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}


export default YouTubeDownloader
