import { useCallback, useEffect, useMemo, useState } from 'react'
import { openVideoWatchInGpm, openUrlInGpm } from './gpmOpener'
import { extractErrorMessage } from './apiError'

export { extractErrorMessage }

const API_BASE = 'http://127.0.0.1:8080'

const STATUS_LABELS = {
  new: 'Mới', drafting: 'Đang soạn', draft_ready: 'Có bản nháp',
  scheduled: 'Đã hẹn đăng', publishing: 'Đang đăng', replied: 'Đã trả lời', skipped: 'Bỏ qua',
  error: 'Lỗi', reconcile_required: 'Cần đối soát',
  review_required: 'Cần duyệt an toàn'
}

const isReplied = comment => (
  comment.status === 'replied' || Boolean(comment.reply_youtube_id)
)

const IMPORT_STATUS_LABELS = {
  new: 'Mới — sẽ nhập và tạo Chat riêng',
  needs_channel: 'Chưa gắn kênh — sẽ xác minh và liên kết',
  needs_chat: 'Đã liên kết — còn thiếu Chat',
  needs_link: 'Đã có Chat — sẽ liên kết kênh',
  needs_link_and_chat: 'Đã có dữ liệu — cần liên kết và tạo Chat',
  existing_chat: 'Đã có trong hệ thống và đã có Chat',
  skipped_error: 'Video đang ở trạng thái Lỗi — bỏ qua',
  channel_conflict: 'Đã liên kết với kênh khác — bỏ qua'
}

const ACTIVE_IMPORT_JOB_STATUSES = new Set(['queued', 'running', 'retry_wait', 'paused'])

function buildLegacyImportRequestMessage(data) {
  const skipped = Number(data.skipped_error || 0)
  const duplicateJobs = Number(data.duplicate_jobs || 0)
  const prefix = data.queued || data.created || data.linked || data.already_ready ? '✅' : '⚠️'
  return (
    `${prefix} Đã xử lý ${data.requested || 0} video: tạo ${data.created || 0}, ` +
    `liên kết ${data.linked || 0}, xếp hàng tạo Chat ${data.queued || 0}, ` +
    `đã sẵn sàng ${data.already_ready || 0}, job đang tồn tại ${duplicateJobs}, ` +
    `bỏ qua ${skipped}.`
  )
}

function buildLegacyImportTerminalMessage(jobs) {
  const succeeded = jobs.filter(job => job.status === 'done').length
  const failedJobs = jobs.filter(job => job.status !== 'done')
  if (!failedJobs.length) {
    return `✅ Đã tạo Chat thành công cho ${succeeded} video.`
  }
  const prefix = succeeded ? '⚠️' : '❌'
  const firstError = failedJobs.find(job => job.error)?.error || ''
  return (
    `${prefix} Tạo Chat hoàn tất: thành công ${succeeded}, thất bại ${failedJobs.length}.` +
    (firstError ? ` ${firstError}` : ' Hãy xem chi tiết trong Trung tâm Job.')
  )
}

export default function YouTubeComments({ onOpenVideo, refreshKey }) {
  const [channels, setChannels] = useState([])
  const [comments, setComments] = useState([])
  const [counts, setCounts] = useState({})
  const [videos, setVideos] = useState([])
  const [promptVersions, setPromptVersions] = useState({})
  const [publications, setPublications] = useState([])
  const [channelFilter, setChannelFilter] = useState('all')
  const [videoFilter, setVideoFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [selectedIds, setSelectedIds] = useState([])
  const [publicationPromptFilter, setPublicationPromptFilter] = useState('all')
  const [publicationForm, setPublicationForm] = useState({ videoId: '', url: '' })
  const [editing, setEditing] = useState({})
  const [importChannelId, setImportChannelId] = useState('')
  const [importPromptVersion, setImportPromptVersion] = useState('')
  const [importUrl, setImportUrl] = useState('')
  const [importItems, setImportItems] = useState([])
  const [importCounts, setImportCounts] = useState({})
  const [importSelectedIds, setImportSelectedIds] = useState([])
  const [importBusy, setImportBusy] = useState(false)
  const [importMessage, setImportMessage] = useState('')
  const [importTrackedJobIds, setImportTrackedJobIds] = useState([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  const loadChannels = useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/youtube-comments/channels`)
    const data = await response.json().catch(() => null)
    if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
    const items = Array.isArray(data?.items) ? data.items : []
    setChannels(items)
  }, [])

  const loadVideos = useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/videos?limit=500&offset=0&video_status=active`)
    const data = await response.json().catch(() => null)
    if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
    const items = Array.isArray(data?.items) ? data.items : []
    setVideos(items)
  }, [])

  const loadPromptVersions = useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/prompts`)
    const data = await response.json().catch(() => null)
    if (!response.ok || !data?.versions) {
      throw new Error(extractErrorMessage(data, 'Không thể tải tên các bộ prompt.'))
    }
    setPromptVersions(data.versions)
  }, [])

  const loadPublications = useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/video-publications`)
    const data = await response.json().catch(() => null)
    if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
    setPublications(Array.isArray(data?.items) ? data.items : [])
  }, [])

  const loadComments = useCallback(async () => {
    const params = new URLSearchParams({ limit: '500' })
    if (channelFilter !== 'all') params.set('channel_id', channelFilter)
    if (videoFilter !== 'all') params.set('video_id', videoFilter)
    if (statusFilter) params.set('status', statusFilter)
    if (debouncedSearch) params.set('search', debouncedSearch)
    const response = await fetch(`${API_BASE}/api/youtube-comments?${params}`)
    const data = await response.json().catch(() => null)
    if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
    setComments(Array.isArray(data?.items) ? data.items : [])
    setCounts(data?.counts || {})
  }, [channelFilter, videoFilter, statusFilter, debouncedSearch])

  const refreshLegacyVideoInventory = useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/youtube-comments/import-preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        channel_id: Number(importChannelId),
        published_url: importUrl.trim()
      })
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
    const items = Array.isArray(data?.items) ? data.items : []
    setImportItems(items)
    setImportCounts(data.counts || {})
    setImportSelectedIds(items.filter(item => item.eligible).map(item => item.youtube_video_id))
    return { data, items }
  }, [importChannelId, importUrl])

  useEffect(() => {
    const timeout = setTimeout(() => setDebouncedSearch(search.trim()), 250)
    return () => clearTimeout(timeout)
  }, [search])

  useEffect(() => {
    Promise.all([
      loadChannels(),
      loadVideos(),
      loadPromptVersions(),
      loadPublications(),
      loadComments()
    ]).catch(error => {
      setMessage(`❌ ${error.message}`)
    })
  }, [loadChannels, loadVideos, loadPromptVersions, loadPublications, loadComments, refreshKey])

  useEffect(() => {
    const interval = setInterval(() => loadComments().catch(() => {}), 5000)
    return () => clearInterval(interval)
  }, [loadComments])

  useEffect(() => {
    const selectableVisibleIds = new Set(
      comments.filter(comment => (
        !isReplied(comment) &&
        !['drafting', 'scheduled', 'publishing', 'reconcile_required'].includes(comment.status)
      )).map(comment => comment.comment_id)
    )
    setSelectedIds(previous => previous.filter(commentId => selectableVisibleIds.has(commentId)))
  }, [comments])

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds])
  const linkedVideoIds = useMemo(
    () => new Set(publications.map(publication => Number(publication.video_id))),
    [publications]
  )
  const unlinkedVideos = useMemo(
    () => videos.filter(video => !linkedVideoIds.has(Number(video.id))),
    [videos, linkedVideoIds]
  )
  const publicationPromptOptions = useMemo(() => {
    const versionIds = new Set(
      videos
        .map(video => String(video.prompt_version || '').trim())
        .filter(Boolean)
    )
    return [...versionIds]
      .map(id => ({
        id,
        name: String(promptVersions[id]?.name || '').trim() || `Bộ prompt cũ (${id})`
      }))
      .sort((left, right) => left.name.localeCompare(right.name, 'vi'))
  }, [videos, promptVersions])
  const filteredUnlinkedVideos = useMemo(
    () => unlinkedVideos.filter(video => (
      publicationPromptFilter === 'all' ||
      String(video.prompt_version || '').trim() === publicationPromptFilter
    )),
    [unlinkedVideos, publicationPromptFilter]
  )
  const selectedImportChannel = useMemo(
    () => channels.find(channel => String(channel.id) === String(importChannelId)),
    [channels, importChannelId]
  )
  const importPromptOptions = useMemo(() => Object.entries(promptVersions)
    .filter(([, version]) => (
      String(version?.default_youtube_channel_id || '') ===
      String(selectedImportChannel?.channel_id || '')
    ))
    .map(([id, version]) => ({ id, name: version?.name || id }))
    .sort((left, right) => left.name.localeCompare(right.name, 'vi')),
  [promptVersions, selectedImportChannel])
  const importEligibleIds = useMemo(
    () => importItems.filter(item => item.eligible).map(item => item.youtube_video_id),
    [importItems]
  )
  const commentVideoOptions = useMemo(() => {
    const itemsByVideoId = new Map()
    publications.forEach(publication => {
      if (
        channelFilter !== 'all' &&
        String(publication.youtube_channel_id) !== String(channelFilter)
      ) return
      if (!itemsByVideoId.has(publication.video_id)) {
        itemsByVideoId.set(publication.video_id, {
          id: publication.video_id,
          title: publication.video_title
        })
      }
    })
    return [...itemsByVideoId.values()]
  }, [publications, channelFilter])
  const selectedVideo = useMemo(
    () => videos.find(video => String(video.id) === String(publicationForm.videoId)),
    [videos, publicationForm.videoId]
  )
  const selectableComments = comments.filter(comment => (
    !isReplied(comment) &&
    !['drafting', 'scheduled', 'publishing', 'reconcile_required'].includes(comment.status)
  ))

  useEffect(() => {
    setPublicationForm(previous => {
      const selectedStillAvailable = filteredUnlinkedVideos.some(
        video => String(video.id) === String(previous.videoId)
      )
      return {
        ...previous,
        videoId: selectedStillAvailable
          ? previous.videoId
          : String(filteredUnlinkedVideos[0]?.id || '')
      }
    })
  }, [filteredUnlinkedVideos])

  useEffect(() => {
    if (!channels.length) {
      setImportChannelId('')
      return
    }
    setImportChannelId(previous => (
      channels.some(channel => String(channel.id) === String(previous))
        ? previous
        : String(channels[0].id)
    ))
  }, [channels])

  useEffect(() => {
    setImportPromptVersion(previous => (
      importPromptOptions.some(version => version.id === previous)
        ? previous
        : (importPromptOptions[0]?.id || '')
    ))
  }, [importPromptOptions])

  const toggleSelected = commentId => {
    setSelectedIds(previous => previous.includes(commentId)
      ? previous.filter(id => id !== commentId)
      : [...previous, commentId])
  }

  const runAction = async (path, payload, successMessage) => {
    setBusy(true)
    setMessage('')
    try {
      const response = await fetch(`${API_BASE}${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: payload ? JSON.stringify(payload) : undefined
      })
      const data = await response.json().catch(() => null)
      if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
      setMessage(`✅ ${successMessage}`)
      setSelectedIds([])
      await loadComments()
    } catch (error) {
      setMessage(`❌ ${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  const sync = async channelId => {
    await runAction(
      `/api/youtube-comments/sync/${channelId}`,
      null,
      'Đã đưa tác vụ đồng bộ vào Trung tâm Job.'
    )
  }

  const syncAll = async () => {
    setBusy(true)
    try {
      for (const channel of channels) {
        const response = await fetch(`${API_BASE}/api/youtube-comments/sync/${channel.id}`, { method: 'POST' })
        const data = await response.json().catch(() => null)
        if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
      }
      setMessage(`✅ Đã đưa ${channels.length} kênh vào hàng đợi đồng bộ.`)
    } catch (error) {
      setMessage(`❌ ${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  const addPublication = async () => {
    setBusy(true)
    try {
      const response = await fetch(
        `${API_BASE}/api/videos/${publicationForm.videoId}/publications`,
        {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            published_url: publicationForm.url
          })
        }
      )
      const data = await response.json().catch(() => null)
      if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
      setPublicationForm(previous => ({ ...previous, url: '' }))
      await Promise.all([loadVideos(), loadPublications()])
      setMessage('✅ Đã gắn link video đã đăng; trạng thái Dashboard được đồng bộ.')
    } catch (error) {
      setMessage(`❌ ${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  const loadLegacyVideos = async () => {
    setImportBusy(true)
    setImportMessage('')
    try {
      const { data, items } = await refreshLegacyVideoInventory()
      setImportMessage(`✅ Đã đối chiếu ${items.length} video; ${data.counts?.eligible || 0} video có thể nhập.`)
    } catch (error) {
      setImportItems([])
      setImportCounts({})
      setImportSelectedIds([])
      setImportMessage(`❌ ${error.message}`)
    } finally {
      setImportBusy(false)
    }
  }

  const startLegacyVideoImport = async () => {
    setImportBusy(true)
    setImportMessage('')
    try {
      const response = await fetch(`${API_BASE}/api/youtube-comments/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          channel_id: Number(importChannelId),
          prompt_version: importPromptVersion,
          youtube_video_ids: importSelectedIds
        })
      })
      const data = await response.json().catch(() => null)
      if (!response.ok || data?.success === false) {
        throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
      }
      const trackedJobIds = Array.isArray(data?.job_ids)
        ? data.job_ids.filter(jobId => typeof jobId === 'string' && jobId)
        : []
      setImportTrackedJobIds(trackedJobIds)
      setImportMessage(buildLegacyImportRequestMessage(data))

      const refreshResults = await Promise.allSettled([
        loadVideos(),
        loadPublications(),
        loadComments()
      ])
      if (refreshResults.some(result => result.status === 'rejected')) {
        setImportMessage(previous => (
          `${previous} ⚠️ Dữ liệu đã được lưu nhưng chưa thể làm mới đầy đủ giao diện.`
        ))
      }
    } catch (error) {
      setImportMessage(`❌ ${error.message}`)
    } finally {
      setImportBusy(false)
    }
  }

  useEffect(() => {
    if (!importTrackedJobIds.length) return undefined
    let stopped = false
    let polling = false

    const pollImportJobs = async () => {
      if (polling) return
      polling = true
      try {
        const jobs = await Promise.all(importTrackedJobIds.map(async jobId => {
          const response = await fetch(`${API_BASE}/api/jobs/${jobId}`)
          const data = await response.json().catch(() => null)
          if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
          return data
        }))
        if (stopped) return

        const activeCount = jobs.filter(job => ACTIVE_IMPORT_JOB_STATUSES.has(job?.status)).length
        const succeeded = jobs.filter(job => job?.status === 'done').length
        const failed = jobs.length - activeCount - succeeded
        if (activeCount) {
          setImportMessage(
            `⏳ Đang tạo Chat: ${activeCount} đang xử lý, ${succeeded} thành công, ${failed} thất bại.`
          )
          return
        }

        setImportMessage(buildLegacyImportTerminalMessage(jobs))
        setImportTrackedJobIds([])
        void Promise.allSettled([
          loadVideos(),
          loadPublications(),
          loadComments(),
          refreshLegacyVideoInventory()
        ])
      } catch (error) {
        if (!stopped) {
          setImportMessage(`⚠️ Job đã được tạo nhưng chưa thể cập nhật trạng thái: ${error.message}`)
        }
      } finally {
        polling = false
      }
    }

    void pollImportJobs()
    const interval = setInterval(pollImportJobs, 2000)
    return () => {
      stopped = true
      clearInterval(interval)
    }
  }, [
    importTrackedJobIds,
    loadComments,
    loadPublications,
    loadVideos,
    refreshLegacyVideoInventory
  ])

  const toggleImportVideo = youtubeVideoId => {
    setImportSelectedIds(previous => previous.includes(youtubeVideoId)
      ? previous.filter(id => id !== youtubeVideoId)
      : [...previous, youtubeVideoId])
  }

  const removePublication = async publication => {
    if (!confirm(`Xóa liên kết ${publication.published_url}? Bình luận đã đồng bộ của link này cũng sẽ bị xóa.`)) return
    setBusy(true)
    try {
      const response = await fetch(
        `${API_BASE}/api/videos/${publication.video_id}/publications/${publication.id}`,
        { method: 'DELETE' }
      )
      const data = await response.json().catch(() => null)
      if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
      await Promise.all([loadVideos(), loadPublications(), loadComments()])
      setMessage('✅ Đã xóa liên kết video đã đăng.')
    } catch (error) {
      setMessage(`❌ ${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  const saveDraft = async comment => {
    const reply = editing[comment.comment_id] ?? comment.draft_reply
    setBusy(true)
    try {
      const response = await fetch(`${API_BASE}/api/youtube-comments/${comment.comment_id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ draft_reply: reply })
      })
      const data = await response.json().catch(() => null)
      if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
      setEditing(previous => {
        const next = { ...previous }; delete next[comment.comment_id]; return next
      })
      await loadComments()
      setMessage('✅ Đã lưu bản nháp.')
    } catch (error) {
      setMessage(`❌ ${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h1 className="hero-title">Bình luận YouTube</h1>
      <p className="hero-subtitle">Quản lý nhiều kênh, soạn trả lời bằng đúng Chat Gốc của từng video.</p>

      <div className="result-panel" style={{ marginTop: 24, padding: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <strong style={{ color: '#a970ff' }}>Liên kết video đã đăng</strong>
          <span style={{ color: '#888', fontSize: '.82em' }}>Link nguồn của Video Fetcher luôn được giữ riêng.</span>
        </div>
        {!unlinkedVideos.length ? (
          <div style={{ color: '#4dd0e1', marginTop: 12 }}>
            Tất cả video hiện tại đã có link đăng.
          </div>
        ) : (
          <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
            <select
              aria-label="Lọc video liên kết theo bộ prompt"
              value={publicationPromptFilter}
              onChange={event => setPublicationPromptFilter(event.target.value)}
            >
              <option value="all">Tất cả bộ prompt</option>
              {publicationPromptOptions.map(version => (
                <option key={version.id} value={version.id}>{version.name}</option>
              ))}
            </select>
            {!filteredUnlinkedVideos.length ? (
              <div style={{ color: '#f5b041' }}>
                Không còn video chưa gắn link trong bộ prompt này.
              </div>
            ) : (
              <>
                <select value={publicationForm.videoId} onChange={event => setPublicationForm({ ...publicationForm, videoId: event.target.value })}>
                  {filteredUnlinkedVideos.map(video => <option key={video.id} value={video.id}>#{video.id} · {video.title}</option>)}
                </select>
                <div className="help-text" style={{ padding: '10px 12px', border: '1px solid #4b4b4b', borderRadius: 6 }}>
                  Kênh theo bộ prompt:{' '}
                  <strong style={{ color: selectedVideo?.default_youtube_channel_db_id ? '#4dd0e1' : '#f5b041' }}>
                    {selectedVideo?.default_youtube_channel_title || 'Chưa gắn kênh — link chưa được xác minh'}
                  </strong>
                  {!selectedVideo?.default_youtube_channel_db_id && (
                    <div style={{ marginTop: 6, color: '#f5b041' }}>
                      Bình luận chưa khả dụng cho đến khi link được nhập lại qua một kênh đã xác minh.
                    </div>
                  )}
                </div>
                <input
                  value={publicationForm.url}
                  onChange={event => setPublicationForm({ ...publicationForm, url: event.target.value })}
                  placeholder="Link chuẩn của video đã đăng: https://www.youtube.com/watch?v=..."
                />
                <button className="btn-run" disabled={busy || !publicationForm.videoId || !publicationForm.url.trim()} onClick={addPublication}>
                  Gắn link đã đăng
                </button>
              </>
            )}
          </div>
        )}
        {!!publications.length && (
          <details style={{ marginTop: 14 }}>
            <summary style={{ color: '#aaa', cursor: 'pointer' }}>
              Quản lý link đã gắn ({publications.length})
            </summary>
            {publications.map(publication => (
              <div key={publication.id} style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10 }}>
                <span style={{ color: publication.youtube_channel_id ? '#aaa' : '#f5b041' }}>
                  {publication.channel_title || 'Chưa gắn kênh — link chưa được xác minh'}:
                </span>
                <button
                  type="button"
                  onClick={async () => {
                    if (publication.video_id) {
                      try {
                        await openVideoWatchInGpm(publication.video_id)
                        setMessage?.('🚀 Đã mở video trên YouTube trong Profile GPM!')
                      } catch (err) {
                        alert(`⚠️ Không thể mở video trong GPM: ${err.message}`)
                      }
                    } else if (publication.gpm_profile_id && publication.published_url) {
                      try {
                        await openUrlInGpm(publication.gpm_profile_id, publication.published_url)
                      } catch (err) {
                        alert(`⚠️ Lỗi mở trong GPM: ${err.message}`)
                      }
                    } else {
                      alert('⚠️ Chưa có GPM Profile liên kết với video/kênh này.')
                    }
                  }}
                  style={{
                    color: '#4dd0e1',
                    background: 'none',
                    border: 'none',
                    textAlign: 'left',
                    cursor: 'pointer',
                    flex: 1,
                    textDecoration: 'underline',
                    padding: 0
                  }}
                  title="Mở video trong GPM Profile của kênh"
                >
                  {publication.published_title || publication.video_title || publication.published_url}
                </button>
                {!publication.youtube_channel_id && (
                  <span style={{ color: '#888', fontSize: '.8em' }}>Bình luận chưa khả dụng</span>
                )}
                <button className="btn-danger" disabled={busy} onClick={() => removePublication(publication)}>Xóa</button>
              </div>
            ))}
          </details>
        )}
      </div>

      <details className="result-panel" style={{ marginTop: 16, padding: 18 }}>
        <summary style={{ color: '#a970ff', cursor: 'pointer', fontWeight: 700 }}>
          Nhập video cũ để trả lời bình luận
        </summary>
        <div className="help-text" style={{ marginTop: 10 }}>
          Để trống link để tải toàn bộ video của kênh. Hệ thống đối chiếu chính xác bằng YouTube Video ID,
          giữ nguyên video và Chat đã thêm trước đây, rồi tạo một Chat riêng cho từng video còn thiếu.
        </div>
        <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
          <select
            aria-label="Kênh nhập video cũ"
            value={importChannelId}
            disabled={importBusy || importTrackedJobIds.length > 0}
            onChange={event => {
              setImportChannelId(event.target.value)
              setImportItems([])
              setImportCounts({})
              setImportSelectedIds([])
              setImportMessage('')
            }}
          >
            {!channels.length && <option value="">Chưa kết nối kênh YouTube</option>}
            {channels.map(channel => (
              <option key={channel.id} value={channel.id}>{channel.title}</option>
            ))}
          </select>
          <select
            aria-label="Bộ prompt cho video cũ"
            value={importPromptVersion}
            disabled={importBusy || importTrackedJobIds.length > 0}
            onChange={event => {
              setImportPromptVersion(event.target.value)
              setImportMessage('')
            }}
          >
            {!importPromptOptions.length && (
              <option value="">Kênh chưa được gắn với bộ prompt trong Settings</option>
            )}
            {importPromptOptions.map(version => (
              <option key={version.id} value={version.id}>{version.name}</option>
            ))}
          </select>
          <input
            value={importUrl}
            disabled={importBusy || importTrackedJobIds.length > 0}
            onChange={event => {
              setImportUrl(event.target.value)
              setImportMessage('')
            }}
            placeholder="Tùy chọn: link một video; để trống để lấy toàn bộ video của kênh"
          />
          <button
            className="btn-secondary"
            disabled={importBusy || importTrackedJobIds.length > 0 || !importChannelId || !importPromptVersion}
            onClick={() => loadLegacyVideos()}
          >
            {importBusy ? 'Đang đối chiếu...' : 'Tải danh sách & kiểm tra trùng'}
          </button>
        </div>

        {importMessage && (
          <div
            role={importMessage.startsWith('❌') ? 'alert' : 'status'}
            aria-live="polite"
            style={{
              marginTop: 12,
              padding: '10px 12px',
              borderRadius: 6,
              border: `1px solid ${importMessage.startsWith('❌') ? '#ff6b6b' : '#4dd0e1'}`,
              color: importMessage.startsWith('❌') ? '#ff6b6b' : '#4dd0e1'
            }}
          >
            {importMessage}
          </div>
        )}

        {!!importItems.length && (
          <div style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
              <label>
                <input
                  type="checkbox"
                  disabled={importBusy || importTrackedJobIds.length > 0}
                  checked={importEligibleIds.length > 0 && importSelectedIds.length === importEligibleIds.length}
                  onChange={event => setImportSelectedIds(event.target.checked ? importEligibleIds : [])}
                />{' '}
                Chọn tất cả có thể nhập ({importEligibleIds.length})
              </label>
              <span style={{ color: '#888' }}>
                Đã có Chat: {importCounts.existing_chat || 0} · Đang chọn: {importSelectedIds.length}
              </span>
              <button
                className="btn-run"
                style={{ width: 'auto', marginLeft: 'auto' }}
                disabled={importBusy || importTrackedJobIds.length > 0 || !importSelectedIds.length || !importPromptVersion}
                onClick={startLegacyVideoImport}
              >
                {importTrackedJobIds.length
                  ? `Đang tạo Chat (${importTrackedJobIds.length})...`
                  : `Nhập ${importSelectedIds.length} video đã chọn`}
              </button>
            </div>
            <div style={{ maxHeight: 420, overflowY: 'auto', marginTop: 12, display: 'grid', gap: 8 }}>
              {importItems.map(item => (
                <label
                  key={item.youtube_video_id}
                  style={{
                    display: 'flex', gap: 10, alignItems: 'center', padding: 10,
                    border: '1px solid #333', borderRadius: 8,
                    opacity: item.eligible ? 1 : 0.65
                  }}
                >
                  <input
                    type="checkbox"
                    disabled={!item.eligible || importBusy}
                    checked={importSelectedIds.includes(item.youtube_video_id)}
                    onChange={() => toggleImportVideo(item.youtube_video_id)}
                  />
                  {item.thumbnail_url && (
                    <img src={item.thumbnail_url} alt="" style={{ width: 80, borderRadius: 5 }} />
                  )}
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <strong style={{ display: 'block' }}>{item.title}</strong>
                    <span style={{ color: item.eligible ? '#4dd0e1' : '#999', fontSize: '.82em' }}>
                      {IMPORT_STATUS_LABELS[item.status] || item.status}
                      {item.existing_video_id ? ` · Video nội bộ #${item.existing_video_id}` : ''}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </div>
        )}
      </details>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', margin: '18px 0' }}>
        <select value={channelFilter} onChange={event => {
          setChannelFilter(event.target.value)
          setVideoFilter('all')
        }}>
          <option value="all">Tất cả kênh</option>
          {channels.map(channel => <option key={channel.id} value={channel.id}>{channel.title}</option>)}
        </select>
        <select value={videoFilter} onChange={event => setVideoFilter(event.target.value)}>
          <option value="all">Tất cả video</option>
          {commentVideoOptions.map(video => (
            <option key={video.id} value={video.id}>#{video.id} · {video.title}</option>
          ))}
        </select>
        <select value={statusFilter} onChange={event => setStatusFilter(event.target.value)}>
          <option value="">Mọi trạng thái</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label} ({counts[value] || 0})</option>)}
        </select>
        <input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Tìm bình luận, người xem, video hoặc link đã đăng..." style={{ flex: 1, minWidth: 260 }} />
        {channelFilter === 'all' ? (
          <button className="btn-secondary" disabled={busy || !channels.length} onClick={syncAll}>↻ Đồng bộ tất cả</button>
        ) : (
          <button className="btn-secondary" disabled={busy} onClick={() => sync(channelFilter)}>↻ Đồng bộ kênh</button>
        )}
      </div>

      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
        <label style={{ color: '#aaa' }}>
          <input
            type="checkbox"
            checked={selectableComments.length > 0 && selectableComments.every(comment => selectedSet.has(comment.comment_id))}
            onChange={event => setSelectedIds(event.target.checked ? selectableComments.map(comment => comment.comment_id) : [])}
          /> Chọn tất cả ({selectedIds.length})
        </label>
        <button className="btn-run" disabled={busy || !selectedIds.length} onClick={() => runAction('/api/youtube-comments/draft', { comment_ids: selectedIds }, 'Đã đưa yêu cầu soạn trả lời vào hàng đợi.')}>✍️ Soạn trả lời</button>
        <button className="btn-run" disabled={busy || !selectedIds.length} onClick={() => runAction('/api/youtube-comments/publish', { comment_ids: selectedIds }, 'Đã đưa bản nháp vào lịch đăng an toàn.')}>📤 Lên lịch đăng</button>
      </div>

      {message && <div style={{ color: message.startsWith('❌') ? '#ff6b6b' : '#4dd0e1', marginBottom: 14 }}>{message}</div>}
      {!comments.length ? (
        <div className="result-panel" style={{ color: '#888' }}>Chưa có bình luận phù hợp. Hãy gắn link đã đăng rồi đồng bộ kênh.</div>
      ) : comments.map(comment => (
        <div key={comment.comment_id} className="result-panel" style={{ padding: 18, marginBottom: 12 }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
            <input type="checkbox" checked={selectedSet.has(comment.comment_id)} disabled={isReplied(comment) || ['drafting', 'scheduled', 'publishing', 'reconcile_required'].includes(comment.status)} onChange={() => toggleSelected(comment.comment_id)} />
            {comment.author_avatar_url && <img src={comment.author_avatar_url} alt="" style={{ width: 38, height: 38, borderRadius: '50%' }} />}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <strong>{comment.author_name || 'Người xem'}</strong>
                <span style={{ color: '#a970ff' }}>{comment.channel_title}</span>
                <span style={{ color: '#4dd0e1' }}>
                  {STATUS_LABELS[isReplied(comment) ? 'replied' : comment.status] || comment.status}
                </span>
              </div>
              <button onClick={() => onOpenVideo(comment.video_id)} style={{ border: 0, padding: 0, marginTop: 5, background: 'transparent', color: '#bbb', textAlign: 'left' }}>
                {comment.video_title}
              </button>
              <div style={{ marginTop: 10, whiteSpace: 'pre-wrap' }}>{comment.text}</div>
              {comment.risk_level === 'review_required' && (
                <div style={{ color: '#ffb74d', marginTop: 8 }}>
                  ⚠️ {comment.risk_reason || 'Bình luận có dấu hiệu chèn lệnh hoặc chứa liên kết; sẽ không tự động đăng.'}
                </div>
              )}
              {comment.auto_reply_reason && (
                <div className="help-text" style={{ marginTop: 7 }}>
                  Tự động: {comment.auto_reply_reason}
                </div>
              )}
              {!comment.chat_url && <div style={{ color: '#ff6b6b', marginTop: 8 }}>Thiếu Chat Gốc — hệ thống sẽ không tạo chat mới thay thế.</div>}
              {isReplied(comment) ? (
                <div style={{ marginTop: 12, padding: 12, background: '#102619', borderRadius: 8 }}>✅ {comment.reply_text}</div>
              ) : (
                <>
                  <textarea
                    value={editing[comment.comment_id] ?? comment.draft_reply ?? ''}
                    onChange={event => setEditing({ ...editing, [comment.comment_id]: event.target.value })}
                    placeholder="Bản nháp trả lời sẽ xuất hiện ở đây..."
                    style={{ width: '100%', minHeight: 80, marginTop: 12 }}
                  />
                  <button className="btn-secondary" disabled={busy || !(editing[comment.comment_id] ?? comment.draft_reply ?? '').trim()} onClick={() => saveDraft(comment)}>💾 Lưu bản nháp</button>
                </>
              )}
              {comment.error && <div style={{ color: '#ff6b6b', marginTop: 8 }}>{comment.error}</div>}
            </div>
          </div>
        </div>
      ))}
    </>
  )
}
