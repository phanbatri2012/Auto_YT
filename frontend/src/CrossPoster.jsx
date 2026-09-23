import { useEffect, useState, useMemo, useCallback, useRef } from 'react'
import './CrossPoster.css'

const API_BASE = 'http://127.0.0.1:8080'
const FB_STORAGE_KEY = 'AUTOYT_FACEBOOK_PAGES'

function withoutFacebookSecrets(page) {
  const { access_token: legacyToken, ...safePage } = page || {}
  return {
    ...safePage,
    token_configured: Boolean(safePage.token_configured || legacyToken)
  }
}

function formatDate(dateStr) {
  if (!dateStr) return '-'
  if (/^\d{8}$/.test(dateStr)) {
    // YYYYMMDD
    return `${dateStr.slice(6, 8)}/${dateStr.slice(4, 6)}/${dateStr.slice(0, 4)}`
  }
  try {
    const d = new Date(dateStr)
    return isNaN(d.getTime()) ? dateStr : d.toLocaleDateString('vi-VN')
  } catch {
    return dateStr
  }
}

function formatTimestamp(ts) {
  if (!ts || ts <= 0) return '-'
  try {
    const d = new Date(ts * 1000)
    return d.toLocaleString('vi-VN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    })
  } catch {
    return '-'
  }
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return ''
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`
}

function normalizeDefaultTags(value) {
  const seen = new Set()
  return String(value || '')
    .split(/[,\n]/)
    .map(tag => tag.trim().replace(/^#+/, '').replace(/\s+/g, ' '))
    .filter(tag => {
      const key = tag.toLocaleLowerCase('vi-VN')
      if (!tag || seen.has(key)) return false
      seen.add(key)
      return true
    })
}

export default function CrossPoster() {
  // Campaigns & Active Page Tab
  const [campaigns, setCampaigns] = useState([])
  const [selectedPageId, setSelectedPageId] = useState('')

  // Settings & Hub States
  const [settings, setSettings] = useState({
    source_channel_id: '',
    source_channel_title: '',
    source_gpm_profile_id: '',
    target_fb_page_id: '',
    target_fb_page_name: '',
    target_gpm_profile_id: '',
    target_access_token: '',
    target_access_token_configured: false,
    daily_quota: 2,
    schedule_times: ['11:30', '19:30'],
    lead_time_minutes: 60,
    post_template: '{title}\n\n{clean_description}\n\n---\n📌 Like & Follow để xem thêm nhiều video hấp dẫn nhé!\n{hashtags}',
    sort_order_mode: 'oldest_first',
    auto_sync_enabled: false,
    auto_sync_type: 'interval',
    auto_sync_interval_hours: 6,
    auto_sync_fixed_times: ['06:00', '18:00'],
    auto_publish_enabled: false,
    convert_to_vertical: false,
    default_tags: [],
    last_synced_at: ''
  })
  const [defaultTagsDraft, setDefaultTagsDraft] = useState('')

  const [stats, setStats] = useState({
    total: 0,
    pending: 0,
    scheduled: 0,
    downloading: 0,
    uploading: 0,
    published: 0,
    skipped: 0,
    error: 0,
    next_scheduled: null
  })

  const [youtubeChannels, setYoutubeChannels] = useState([])
  const [gpmProfiles, setGpmProfiles] = useState([])
  const [facebookPages, setFacebookPages] = useState([])

  // Batch Cloud Pre-Scheduler State
  const [daysAhead, setDaysAhead] = useState(7)
  const [preScheduleTask, setPreScheduleTask] = useState({
    status: 'idle', // 'idle' | 'starting' | 'running' | 'completed' | 'canceled' | 'error'
    current_index: 0,
    total_items: 0,
    completed_count: 0,
    progress_percent: 0,
    current_video_title: '',
    phase: '',
    message: '',
    error: ''
  })

  // Queue & Pagination States
  const [queueItems, setQueueItems] = useState([])
  const [queueTotal, setQueueTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(50)
  const [totalPages, setTotalPages] = useState(1)
  const [statusFilter, setStatusFilter] = useState('all')
  const [searchQuery, setSearchQuery] = useState('')

  // UI & Loading States
  const [isSettingsOpen, setIsSettingsOpen] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [isPublishingId, setIsPublishingId] = useState(null)
  const [isTestingFb, setIsTestingFb] = useState(false)
  const [isExtractingToken, setIsExtractingToken] = useState(false)
  const [message, setMessage] = useState('')
  const [messageType, setMessageType] = useState('info') // 'info' | 'success' | 'error'

  // Time Slot Adder State
  const [isAddingTime, setIsAddingTime] = useState(false)
  const [newTimeSlot, setNewTimeSlot] = useState('')

  // Edit Modal State
  const [editingItem, setEditingItem] = useState(null)
  const [editForm, setEditForm] = useState({
    fb_title: '',
    fb_description: '',
    scheduled_datetime_local: ''
  })

  // New Campaign Modal State
  const [isNewCampaignModalOpen, setIsNewCampaignModalOpen] = useState(false)
  const [newCampaignForm, setNewCampaignForm] = useState({
    page_id: '',
    page_name: '',
    access_token: '',
    gpm_profile_id: '',
    source_channel_id: '',
    source_channel_title: '',
    daily_quota: 2
  })

  // Load Settings & Hub Data for active Fanpage
  const loadSettingsAndHub = useCallback(async (pageId = selectedPageId) => {
    try {
      // Load Facebook Pages saved from Channel Hub localStorage
      try {
        const savedFb = localStorage.getItem(FB_STORAGE_KEY)
        if (savedFb) {
          const parsedPages = JSON.parse(savedFb)
          if (Array.isArray(parsedPages)) {
            const legacyPages = parsedPages.filter(
              page => page?.access_token && page?.page_id
            )
            if (legacyPages.length > 0) {
              await Promise.all(legacyPages.map(async page => {
                const response = await fetch(
                  `${API_BASE}/api/fb-crossposter/settings?page_id=${encodeURIComponent(page.page_id)}`,
                  {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      target_fb_page_id: page.page_id,
                      target_fb_page_name: page.name || page.page_id,
                      target_gpm_profile_id: page.gpm_profile_id || '',
                      target_access_token: page.access_token
                    })
                  }
                )
                if (!response.ok) throw new Error(`HTTP ${response.status}`)
              }))
            }
            const safePages = parsedPages.map(withoutFacebookSecrets)
            localStorage.setItem(FB_STORAGE_KEY, JSON.stringify(safePages))
            setFacebookPages(safePages)
          }
        }
      } catch (e) {
        console.error('Failed to parse localStorage FB pages', e)
      }

      const params = new URLSearchParams()
      if (pageId) params.append('page_id', pageId)

      const res = await fetch(`${API_BASE}/api/fb-crossposter/settings?${params.toString()}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()

      if (data.settings) {
        setSettings(prev => ({ ...prev, ...data.settings, target_access_token: '' }))
        setDefaultTagsDraft(
          Array.isArray(data.settings.default_tags)
            ? data.settings.default_tags.join(', ')
            : ''
        )
      }
      if (data.stats) {
        setStats(data.stats)
      }
      if (data.campaigns) {
        setCampaigns(data.campaigns)
        // If no page currently selected, set to first campaign
        if (!pageId && data.campaigns.length > 0) {
          const firstPid = data.campaigns[0].page_id || ''
          setSelectedPageId(firstPid)
        }
      }
      if (data.youtube_channels) {
        const ytList = Array.isArray(data.youtube_channels) 
          ? data.youtube_channels 
          : (Array.isArray(data.youtube_channels.items) ? data.youtube_channels.items : [])
        setYoutubeChannels(ytList)
      }
      if (data.gpm_profiles) {
        const gpmList = Array.isArray(data.gpm_profiles) 
          ? data.gpm_profiles 
          : (Array.isArray(data.gpm_profiles.items) ? data.gpm_profiles.items : [])
        setGpmProfiles(gpmList)
      }
    } catch (err) {
      console.error('Failed to load Cross-Poster settings:', err)
    }
  }, [selectedPageId])

  // Load Queue List for active Fanpage
  const loadQueue = useCallback(async (pageId = selectedPageId) => {
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
        search: searchQuery
      })
      if (pageId) {
        params.append('target_page_id', pageId)
      }
      if (statusFilter !== 'all') {
        params.append('status', statusFilter)
      }

      const res = await fetch(`${API_BASE}/api/fb-crossposter/queue?${params.toString()}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()

      setQueueItems(data.items || [])
      setQueueTotal(data.total || 0)
      setTotalPages(data.total_pages || 1)
      if (data.stats) setStats(data.stats)
    } catch (err) {
      console.error('Failed to load queue:', err)
    }
  }, [selectedPageId, page, pageSize, statusFilter, searchQuery])

  // Check Batch Pre-Scheduler Status
  const checkPreScheduleStatus = useCallback(async () => {
    try {
      const params = new URLSearchParams()
      if (selectedPageId) params.append('page_id', selectedPageId)
      const res = await fetch(`${API_BASE}/api/fb-crossposter/schedule-ahead/status?${params.toString()}`)
      if (!res.ok) return
      const data = await res.json()
      if (data && data.status) {
        setPreScheduleTask(data)
        if (data.status === 'completed' || data.status === 'canceled' || data.status === 'error') {
          loadQueue()
        }
      }
    } catch (err) {
      console.error('Failed to check pre-schedule status:', err)
    }
  }, [selectedPageId, loadQueue])

  useEffect(() => {
    loadSettingsAndHub()
  }, [loadSettingsAndHub])

  useEffect(() => {
    loadQueue()
  }, [loadQueue])

  // Poll Pre-Scheduler status when running
  useEffect(() => {
    checkPreScheduleStatus()
    let timer = null
    if (preScheduleTask.status === 'running' || preScheduleTask.status === 'starting') {
      timer = setInterval(() => {
        checkPreScheduleStatus()
      }, 2500)
    }
    return () => {
      if (timer) clearInterval(timer)
    }
  }, [preScheduleTask.status, checkPreScheduleStatus])

  // Switch Active Fanpage Tab
  const handleSelectTab = (pageId) => {
    setSelectedPageId(pageId)
    setPage(1)
    loadSettingsAndHub(pageId)
    loadQueue(pageId)
  }

  // Delete Fanpage Campaign
  const handleDeleteCampaign = async (e, pageId, pageName) => {
    e.stopPropagation()
    if (!window.confirm(`Bạn có chắc chắn muốn xóa Fanpage "${pageName || pageId}" và toàn bộ hàng đợi của Fanpage này?`)) {
      return
    }
    try {
      const res = await fetch(`${API_BASE}/api/fb-crossposter/campaigns/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ page_id: pageId })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setMessage(data.message || 'Đã xóa chiến dịch thành công')
      setMessageType('success')
      const remaining = data.campaigns || []
      setCampaigns(remaining)
      if (selectedPageId === pageId) {
        const nextPid = remaining.length > 0 ? (remaining[0].page_id || '') : ''
        setSelectedPageId(nextPid)
        loadSettingsAndHub(nextPid)
        loadQueue(nextPid)
      }
    } catch (err) {
      alert(`Lỗi xóa chiến dịch: ${err.message}`)
    }
  }

  // Save Settings for active Fanpage
  const handleSaveSettings = async () => {
    try {
      // Auto-commit pending newTimeSlot if user selected a time but didn't click "+ Thêm giờ"
      let currentTimes = Array.isArray(settings.schedule_times) ? [...settings.schedule_times] : []
      if (newTimeSlot && /^\d{1,2}:\d{2}$/.test(newTimeSlot.trim())) {
        const clean = newTimeSlot.trim().padStart(5, '0')
        if (!currentTimes.includes(clean)) {
          currentTimes.push(clean)
          currentTimes.sort()
        }
      }

      const settingsToSave = {
        ...settings,
        schedule_times: currentTimes,
        default_tags: normalizeDefaultTags(defaultTagsDraft)
      }

      const params = new URLSearchParams()
      if (selectedPageId) params.append('page_id', selectedPageId)
      const res = await fetch(`${API_BASE}/api/fb-crossposter/settings?${params.toString()}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settingsToSave)
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || `HTTP ${res.status}`)
      }
      const data = await res.json()
      if (data.settings) {
        setSettings(prev => ({ ...prev, ...data.settings, target_access_token: '' }))
        setDefaultTagsDraft(
          Array.isArray(data.settings.default_tags)
            ? data.settings.default_tags.join(', ')
            : ''
        )
      }
      setNewTimeSlot('')
      setIsAddingTime(false)
      setMessage(`Đã lưu cấu hình thành công cho Fanpage: ${data.settings?.target_fb_page_name || data.settings?.target_fb_page_id || 'Mặc định'}`)
      setMessageType('success')
      loadSettingsAndHub(selectedPageId)
    } catch (err) {
      setMessage(`Lỗi lưu cấu hình: ${err.message}`)
      setMessageType('error')
    }
  }

  // Test Facebook Connection
  const handleTestFbConnection = async () => {
    if (
      !settings.target_fb_page_id ||
      (!settings.target_access_token && !settings.target_access_token_configured)
    ) {
      setMessage('Vui lòng nhập hoặc chọn Fanpage ID và Access Token trước khi kiểm tra')
      setMessageType('error')
      return
    }
    setIsTestingFb(true)
    setMessage('')
    try {
      const res = await fetch(`${API_BASE}/api/fb-crossposter/test-connection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          page_id: settings.target_fb_page_id,
          access_token: settings.target_access_token,
          gpm_profile_id: settings.target_gpm_profile_id
        })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(`✅ Kết nối thành công tới Fanpage: "${data.page_name}" (${data.category || 'Page'}) qua ${data.proxy_used}`)
      setMessageType('success')
      // Auto fill page name if empty
      if (!settings.target_fb_page_name && data.page_name) {
        setSettings(prev => ({ ...prev, target_fb_page_name: data.page_name }))
      }
    } catch (err) {
      setMessage(`❌ Lỗi kết nối Fanpage: ${err.message}`)
      setMessageType('error')
    } finally {
      setIsTestingFb(false)
    }
  }

  // Auto-extract a Page token via Playwright CDP and keep it backend-only.
  const handleAutoExtractToken = async () => {
    const profileId = settings.target_gpm_profile_id
    const targetPageId = settings.target_fb_page_id || selectedPageId
    if (!targetPageId || !profileId) {
      setMessage('Vui lòng chọn Fanpage Page ID và Profile trình duyệt trước khi lấy Token')
      setMessageType('error')
      return
    }
    setIsExtractingToken(true)
    setMessage(`⏳ Đang kết nối trình duyệt (${profileId}) và lấy Page Access Token...`)
    setMessageType('info')
    try {
      const res = await fetch(`${API_BASE}/api/fb-crossposter/extract-token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile_id: profileId,
          target_page_id: targetPageId
        })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      if (data.success && data.pages && data.pages.length > 0) {
        const matched = data.matched_page
        if (matched) {
          setSettings(prev => ({
            ...prev,
            target_access_token: '',
            target_access_token_configured: Boolean(matched.token_configured),
            target_fb_page_id: matched.page_id || prev.target_fb_page_id,
            target_fb_page_name: matched.name || prev.target_fb_page_name,
            target_gpm_profile_id: profileId
          }))
          setMessage(`✅ ${data.message || `Đã lấy và lưu Page Access Token cho Fanpage "${matched.name}"!`}`)
          setMessageType('success')
          loadSettingsAndHub(selectedPageId)
        }
      } else {
        setMessage(`⚠️ ${data.message || 'Không tìm thấy Fanpage nào trong phiên trình duyệt.'}`)
        setMessageType('error')
      }
    } catch (err) {
      setMessage(`❌ Lỗi tự động lấy Token: ${err.message}`)
      setMessageType('error')
    } finally {
      setIsExtractingToken(false)
    }
  }

  // Scan YouTube Channel
  const handleSyncChannel = async () => {
    if (!settings.source_channel_id) {
      setMessage('Vui lòng chọn hoặc nhập URL Kênh YouTube nguồn')
      setMessageType('error')
      return
    }
    setIsSyncing(true)
    setMessage('Đang kết nối YouTube và quét danh sách video công khai...')
    setMessageType('info')
    try {
      const params = new URLSearchParams()
      if (selectedPageId) params.append('page_id', selectedPageId)
      const res = await fetch(`${API_BASE}/api/fb-crossposter/sync?${params.toString()}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          channel_url: settings.source_channel_id,
          gpm_profile_id: settings.source_gpm_profile_id,
          sort_order_mode: settings.sort_order_mode,
          target_page_id: selectedPageId
        })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(data.message)
      setMessageType('success')
      loadQueue(selectedPageId)
      loadSettingsAndHub(selectedPageId)
    } catch (err) {
      setMessage(`Lỗi quét kênh: ${err.message}`)
      setMessageType('error')
    } finally {
      setIsSyncing(false)
    }
  }

  // Recalculate Schedule
  const handleRecalculateSchedule = async () => {
    try {
      const params = new URLSearchParams()
      if (selectedPageId) params.append('page_id', selectedPageId)
      const res = await fetch(`${API_BASE}/api/fb-crossposter/recalculate-schedule?${params.toString()}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          daily_quota: settings.daily_quota,
          schedule_times: settings.schedule_times,
          target_page_id: selectedPageId
        })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(data.message)
      setMessageType('success')
      loadQueue(selectedPageId)
    } catch (err) {
      setMessage(`Lỗi tính toán lịch: ${err.message}`)
      setMessageType('error')
    }
  }

  // Start Batch Cloud Pre-Scheduler
  const handleStartPreSchedule = async () => {
    if (!settings.target_access_token && !settings.target_access_token_configured) {
      setMessage('⚠️ Chưa có Page Access Token. Vui lòng dán Access Token vào ô "Page Access Token" ở trên, bấm [Kiểm tra] và [Lưu Cấu Hình] trước khi bắt đầu!')
      setMessageType('error')
      return
    }
    const totalVideos = daysAhead * (settings.daily_quota || 2)
    if (!window.confirm(`Xác nhận bắt đầu đưa trước ${totalVideos} video (${daysAhead} ngày) lên lịch phát sóng Meta Cloud cho Fanpage này?`)) {
      return
    }
    try {
      const res = await fetch(`${API_BASE}/api/fb-crossposter/schedule-ahead`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          page_id: selectedPageId,
          days_ahead: daysAhead
        })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      if (data.task) setPreScheduleTask(data.task)
      setMessage(`🚀 Đã khởi động tác vụ đưa trước ${daysAhead} ngày video lên Meta Cloud!`)
      setMessageType('success')
    } catch (err) {
      setMessage(`Lỗi khởi động đặt lịch trước: ${err.message}`)
      setMessageType('error')
    }
  }

  // Cancel Batch Cloud Pre-Scheduler
  const handleCancelPreSchedule = async () => {
    if (!window.confirm('Bạn có chắc chắn muốn dừng tác vụ đặt lịch trước? Các video đã tải lên Meta thành công sẽ vẫn được giữ nguyên.')) {
      return
    }
    try {
      const res = await fetch(`${API_BASE}/api/fb-crossposter/schedule-ahead/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ page_id: selectedPageId })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setMessage('Đã gửi yêu cầu dừng tác vụ đặt lịch trước.')
      setMessageType('info')
      checkPreScheduleStatus()
    } catch (err) {
      alert(`Lỗi dừng tác vụ: ${err.message}`)
    }
  }

  // Publish Item Now
  const handlePublishNow = async (itemId) => {
    if (!window.confirm('Bạn có chắc chắn muốn tải và đăng video này ngay lập tức lên nền tảng đích?')) {
      return
    }
    setIsPublishingId(itemId)
    setMessage(`Đang tải video #${itemId} và xuất bản lên nền tảng đích qua giao thức Resumable Upload...`)
    setMessageType('info')
    try {
      const res = await fetch(`${API_BASE}/api/fb-crossposter/queue/${itemId}/publish-now`, {
        method: 'POST'
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(`✅ Đã đăng thành công video #${itemId}!`)
      setMessageType('success')
      loadQueue(selectedPageId)
    } catch (err) {
      setMessage(`❌ Lỗi đăng video #${itemId}: ${err.message}`)
      setMessageType('error')
      loadQueue(selectedPageId)
    } finally {
      setIsPublishingId(null)
    }
  }

  // Skip / Unskip Item
  const handleToggleSkip = async (item) => {
    const isSkipped = item.status === 'skipped'
    const endpoint = isSkipped ? 'unskip' : 'skip'
    try {
      const res = await fetch(`${API_BASE}/api/fb-crossposter/queue/${item.id}/${endpoint}`, {
        method: 'POST'
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      loadQueue(selectedPageId)
    } catch (err) {
      alert(`Thao tác thất bại: ${err.message}`)
    }
  }

  // Open Edit Modal
  const handleOpenEdit = (item) => {
    let dtStr = ''
    if (item.scheduled_publish_time && item.scheduled_publish_time > 0) {
      const d = new Date(item.scheduled_publish_time * 1000)
      const pad = (n) => String(n).padStart(2, '0')
      dtStr = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
    }
    setEditingItem(item)
    setEditForm({
      fb_title: item.fb_title || item.original_title || '',
      fb_description: item.fb_description || '',
      scheduled_datetime_local: dtStr
    })
  }

  // Save Edit Item
  const handleSaveEdit = async () => {
    if (!editingItem) return
    try {
      let ts = null
      if (editForm.scheduled_datetime_local) {
        ts = Math.floor(new Date(editForm.scheduled_datetime_local).getTime() / 1000)
      }
      const res = await fetch(`${API_BASE}/api/fb-crossposter/queue/${editingItem.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fb_title: editForm.fb_title,
          fb_description: editForm.fb_description,
          scheduled_publish_time: ts
        })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setEditingItem(null)
      loadQueue(selectedPageId)
    } catch (err) {
      alert(`Lỗi lưu thay đổi: ${err.message}`)
    }
  }

  // Delete Item
  const handleDeleteItem = async (itemId) => {
    if (!window.confirm(`Xóa video #${itemId} khỏi hàng đợi?`)) return
    try {
      const res = await fetch(`${API_BASE}/api/fb-crossposter/queue/${itemId}`, {
        method: 'DELETE'
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      loadQueue(selectedPageId)
    } catch (err) {
      alert(`Lỗi xóa: ${err.message}`)
    }
  }

  // Create New Campaign
  const handleCreateNewCampaign = async () => {
    if (!newCampaignForm.page_id || !newCampaignForm.access_token) {
      alert('Vui lòng nhập Fanpage ID và Access Token')
      return
    }
    try {
      const res = await fetch(`${API_BASE}/api/fb-crossposter/settings?page_id=${encodeURIComponent(newCampaignForm.page_id)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_fb_page_id: newCampaignForm.page_id,
          target_fb_page_name: newCampaignForm.page_name || `Fanpage ${newCampaignForm.page_id}`,
          target_access_token: newCampaignForm.access_token,
          target_gpm_profile_id: newCampaignForm.gpm_profile_id,
          source_channel_id: newCampaignForm.source_channel_id,
          source_channel_title: newCampaignForm.source_channel_title,
          daily_quota: newCampaignForm.daily_quota || 2,
          lead_time_minutes: 60
        })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setIsNewCampaignModalOpen(false)
      setSelectedPageId(newCampaignForm.page_id)
      setNewCampaignForm(prev => ({ ...prev, access_token: '' }))
      loadSettingsAndHub(newCampaignForm.page_id)
      setMessage(`Đã thêm thành công chiến dịch cho Fanpage: ${newCampaignForm.page_name || newCampaignForm.page_id}`)
      setMessageType('success')
    } catch (err) {
      alert(`Lỗi tạo chiến dịch: ${err.message}`)
    }
  }

  // Time Slot Management
  const handleAddTimeSlot = (timeVal) => {
    const slotToAdd = (typeof timeVal === 'string' && timeVal) ? timeVal : newTimeSlot
    if (!slotToAdd || !/^\d{1,2}:\d{2}$/.test(slotToAdd.trim())) {
      setMessage('Vui lòng chọn hoặc nhập giờ hợp lệ theo định dạng HH:MM (VD: 14:30)')
      setMessageType('error')
      return false
    }
    const clean = slotToAdd.trim().padStart(5, '0')
    const currentTimes = Array.isArray(settings.schedule_times) ? settings.schedule_times : []
    if (!currentTimes.includes(clean)) {
      const updated = [...currentTimes, clean].sort()
      setSettings(prev => ({ ...prev, schedule_times: updated }))
    }
    setNewTimeSlot('')
    setIsAddingTime(false)
    return true
  }

  const handleCancelAddTime = () => {
    setNewTimeSlot('')
    setIsAddingTime(false)
  }

  const handleRemoveTimeSlot = (slot) => {
    const currentTimes = Array.isArray(settings.schedule_times) ? settings.schedule_times : []
    const updated = currentTimes.filter(s => s !== slot)
    setSettings(prev => ({ ...prev, schedule_times: updated }))
  }

  // Template Quick Insert Tag
  const handleInsertTag = (tag) => {
    setSettings(prev => ({
      ...prev,
      post_template: (prev.post_template || '') + ` ${tag} `
    }))
  }

  // Handle Channel Hub Selectors
  const handleSelectYoutubeChannel = (channelId) => {
    const found = youtubeChannels.find(c => c.channel_id === channelId || String(c.id) === channelId)
    if (found) {
      setSettings(prev => ({
        ...prev,
        source_channel_id: found.channel_id ? `https://www.youtube.com/channel/${found.channel_id}` : prev.source_channel_id,
        source_channel_title: found.channel_title || found.name || '',
        source_gpm_profile_id: found.gpm_profile_id || ''
      }))
    }
  }

  const handleSelectFacebookPage = (pageId) => {
    const found = facebookPages.find(p => p.page_id === pageId)
    if (found) {
      setSelectedPageId(found.page_id)
      setSettings(prev => ({
        ...prev,
        target_fb_page_id: found.page_id,
        target_fb_page_name: found.name || '',
        target_access_token: '',
        target_access_token_configured: Boolean(found.token_configured),
        target_gpm_profile_id: found.gpm_profile_id || ''
      }))
      loadSettingsAndHub(found.page_id)
      loadQueue(found.page_id)
    }
  }

  return (
    <div className="fb-crossposter-container">
      {/* Alert Notification Message */}
      {message && (
        <div style={{
          padding: '12px 18px',
          borderRadius: '8px',
          background: messageType === 'error' ? 'rgba(239, 68, 68, 0.15)' : messageType === 'success' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(56, 189, 248, 0.15)',
          border: `1px solid ${messageType === 'error' ? 'rgba(239, 68, 68, 0.4)' : messageType === 'success' ? 'rgba(16, 185, 129, 0.4)' : 'rgba(56, 189, 248, 0.4)'}`,
          color: messageType === 'error' ? '#fca5a5' : messageType === 'success' ? '#6ee7b7' : '#7dd3fc',
          fontSize: '0.9rem',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center'
        }}>
          <span>{message}</span>
          <button
            onClick={() => setMessage('')}
            style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', fontSize: '1.1rem' }}
          >✕</button>
        </div>
      )}

      {/* Multi-Fanpage Campaign Tabs Bar */}
      <div className="fb-campaigns-tabs">
        <span style={{ fontSize: '0.85rem', color: '#94a3b8', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Fanpage:
        </span>
        {campaigns.map(camp => (
          <button
            key={camp.page_id || 'default'}
            className={`fb-campaign-tab ${selectedPageId === (camp.page_id || '') ? 'active' : ''}`}
            onClick={() => handleSelectTab(camp.page_id || '')}
          >
            <span>📘 {camp.page_name || camp.target_fb_page_name || 'Mặc định'} {camp.page_id ? `(..${camp.page_id.slice(-6)})` : ''}</span>
            <span className="fb-tab-badge">{camp.stats?.total || 0}</span>
            {campaigns.length > 1 && (
              <span
                className="fb-tab-delete-btn"
                title={`Xóa chiến dịch Fanpage "${camp.page_name || camp.page_id}"`}
                onClick={(e) => handleDeleteCampaign(e, camp.page_id, camp.page_name || camp.target_fb_page_name)}
              >
                ✕
              </span>
            )}
          </button>
        ))}
        <button
          className="fb-btn fb-btn-secondary fb-btn-sm"
          onClick={() => setIsNewCampaignModalOpen(true)}
          style={{ whiteSpace: 'nowrap' }}
        >
          + Thêm Fanpage Mới
        </button>
      </div>

      {/* Main Selection & Action Panel */}
      <div className="fb-panel">
        <div className="fb-panel-header">
          <div className="fb-panel-title">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
            Cross-Poster: Multi-Platform Video Syndication
          </div>
          <div style={{ display: 'flex', gap: '10px' }}>
            <button
              className="fb-btn fb-btn-success"
              onClick={handleSaveSettings}
              title="Lưu toàn bộ cấu hình kênh nguồn, fanpage đích, quota và mẫu bài viết"
            >
              💾 Lưu Cấu Hình
            </button>
            <button
              className="fb-btn fb-btn-primary"
              onClick={handleSyncChannel}
              disabled={isSyncing}
            >
              {isSyncing ? '⏳ Đang quét...' : '🔍 Quét Kênh YouTube'}
            </button>
            <button
              className="fb-btn fb-btn-secondary"
              onClick={handleRecalculateSchedule}
            >
              ⚡ Tính Lại Lịch Đăng
            </button>
          </div>
        </div>

        {/* Source & Target Grid */}
        <div className="fb-selector-grid">
          {/* Source YouTube Channel */}
          <div className="fb-form-group">
            <label>
              <span>1. Kênh YouTube Nguồn (Công khai)</span>
              {settings.source_gpm_profile_id ? (
                <span className="badge-network badge-gpm" title={`Đang định tuyến qua Proxy của Profile GPM: ${settings.source_gpm_profile_id}`}>
                  🔒 GPM Proxy ({settings.source_gpm_profile_id})
                </span>
              ) : (
                <span className="badge-network badge-local" title="Đang tải video trực tiếp qua mạng Local của máy tính (Direct IP)">
                  🌐 Direct / Local
                </span>
              )}
            </label>
            {youtubeChannels.length > 0 && (
              <select
                className="fb-select"
                onChange={(e) => handleSelectYoutubeChannel(e.target.value)}
                defaultValue=""
              >
                <option value="" disabled>-- Chọn kênh đã kết nối từ Channel Hub --</option>
                {youtubeChannels.map((c) => (
                  <option key={c.id || c.channel_id} value={c.channel_id || c.id}>
                    {c.channel_title || c.name || c.channel_id} {c.gpm_profile_id ? `[GPM: ${c.gpm_profile_id}]` : ''}
                  </option>
                ))}
              </select>
            )}
            <input
              type="text"
              className="fb-input"
              placeholder="Hoặc dán URL kênh YouTube: https://www.youtube.com/@ChannelName"
              value={settings.source_channel_id}
              onChange={(e) => setSettings(prev => ({ ...prev, source_channel_id: e.target.value }))}
            />
            <div className="fb-proxy-selector" style={{ marginTop: '6px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '0.78rem', color: '#94a3b8', whiteSpace: 'nowrap' }}>🛡️ Tải video qua GPM:</span>
              <select
                className="fb-select"
                style={{ flex: 1, padding: '4px 8px', fontSize: '0.78rem', height: '32px' }}
                value={settings.source_gpm_profile_id || ''}
                onChange={(e) => setSettings(prev => ({ ...prev, source_gpm_profile_id: e.target.value }))}
              >
                <option value="">🌐 Trực tiếp (Direct Local IP - Không Proxy)</option>
                {Array.isArray(gpmProfiles) && gpmProfiles.map(p => (
                  <option key={p.id} value={p.id}>
                    🔒 [GPM] {p.name || p.id} {p.raw_proxy ? `(${p.raw_proxy})` : ''}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Target Multi-Platform Destination */}
          <div className="fb-form-group">
            <label>
              <span>2. Nền tảng Đích ({settings.target_fb_page_name || 'Facebook Fanpage'})</span>
              {settings.target_gpm_profile_id ? (
                <span className="badge-network badge-gpm" title={`Đang định tuyến qua Proxy của Profile GPM: ${settings.target_gpm_profile_id}`}>
                  🔒 GPM Proxy ({settings.target_gpm_profile_id})
                </span>
              ) : (
                <span className="badge-network badge-local" title="Đang thao tác trực tiếp qua kết nối mạng Local của máy tính">
                  💻 Direct Local IP
                </span>
              )}
            </label>
            {Array.isArray(facebookPages) && facebookPages.length > 0 && (
              <select
                className="fb-select"
                onChange={(e) => handleSelectFacebookPage(e.target.value)}
                defaultValue=""
              >
                <option value="" disabled>-- Chọn Fanpage đã kết nối từ Channel Hub --</option>
                {facebookPages.map((p) => (
                  <option key={p.page_id} value={p.page_id}>
                    {p.name || p.page_id} {p.gpm_profile_id ? `[GPM: ${p.gpm_profile_id}]` : '[Local IP]'}
                  </option>
                ))}
              </select>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
              <input
                type="text"
                className="fb-input"
                placeholder="Fanpage Page ID"
                value={settings.target_fb_page_id}
                onChange={(e) => setSettings(prev => ({ ...prev, target_fb_page_id: e.target.value }))}
              />
              <div style={{ display: 'flex', gap: '6px' }}>
                <input
                  type="password"
                  className="fb-input"
                  placeholder="Page Access Token"
                  value={settings.target_access_token}
                  onChange={(e) => setSettings(prev => ({ ...prev, target_access_token: e.target.value }))}
                />
                <button
                  className="fb-btn fb-btn-magic fb-btn-sm"
                  onClick={handleAutoExtractToken}
                  disabled={isExtractingToken}
                  title="Lấy Page Access Token và lưu mã hóa tại backend"
                  style={{ whiteSpace: 'nowrap' }}
                >
                  {isExtractingToken ? '⏳ Đang lấy...' : '🤖 1-Click Lấy Page Token'}
                </button>
                <button
                  className="fb-btn fb-btn-secondary fb-btn-sm"
                  onClick={handleTestFbConnection}
                  disabled={isTestingFb}
                  title="Kiểm tra kết nối Fanpage"
                >
                  {isTestingFb ? '...' : 'Kiểm tra'}
                </button>
              </div>
            </div>
            {(settings.target_access_token || settings.target_access_token_configured) && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '4px', fontSize: '0.78rem', color: '#34d399' }}>
                <span>🔒 Page Access Token đã được lưu mã hóa</span>
              </div>
            )}
            <div className="fb-proxy-selector" style={{ marginTop: '6px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '0.78rem', color: '#94a3b8', whiteSpace: 'nowrap' }}>🛡️ Đăng bài qua GPM:</span>
              <select
                className="fb-select"
                style={{ flex: 1, padding: '4px 8px', fontSize: '0.78rem', height: '32px' }}
                value={settings.target_gpm_profile_id || ''}
                onChange={(e) => setSettings(prev => ({ ...prev, target_gpm_profile_id: e.target.value }))}
              >
                <option value="">💻 Trực tiếp (Direct Local IP)</option>
                {Array.isArray(gpmProfiles) && gpmProfiles.map(p => (
                  <option key={p.id} value={p.id}>
                    🔒 [GPM] {p.name || p.id} {p.raw_proxy ? `(${p.raw_proxy})` : ''}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </div>

      {/* Progress & Stats Banner */}
      <div className="fb-stats-grid">
        <div className="fb-stat-card">
          <div className="fb-stat-label">Tổng video công khai</div>
          <div className="fb-stat-value">{stats.total}</div>
        </div>
        <div className="fb-stat-card">
          <div className="fb-stat-label">Đã đăng thành công</div>
          <div className="fb-stat-value published">{stats.published}</div>
        </div>
        <div className="fb-stat-card">
          <div className="fb-stat-label">Đã lên lịch</div>
          <div className="fb-stat-value scheduled">{stats.scheduled}</div>
        </div>
        <div className="fb-stat-card">
          <div className="fb-stat-label">Đang trong hàng đợi</div>
          <div className="fb-stat-value pending">{stats.pending}</div>
        </div>
        <div className="fb-stat-card">
          <div className="fb-stat-label">Bỏ qua</div>
          <div className="fb-stat-value skipped">{stats.skipped}</div>
        </div>
      </div>

      {/* Batch Cloud Pre-Scheduler Card ("Lên lịch trước cho N ngày") */}
      <div className="fb-preschedule-card">
        <div className="fb-preschedule-header">
          <div className="fb-preschedule-title">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
            Lên Lịch Trước N Ngày (Batch Cloud Pre-Scheduler)
          </div>
          <span style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
            Tự động tải & đưa video lên Cloud Meta theo thứ tự 1-by-1 (Sau khi xong có thể tắt máy tính)
          </span>
        </div>

        <div className="fb-preschedule-grid">
          <div className="fb-days-input-group">
            <span style={{ fontSize: '0.88rem', color: '#cbd5e1', fontWeight: '600' }}>Đặt trước:</span>
            <input
              type="number"
              min="1"
              max="60"
              className="fb-days-input"
              value={daysAhead}
              disabled={preScheduleTask.status === 'running' || preScheduleTask.status === 'starting'}
              onChange={(e) => setDaysAhead(Math.max(1, parseInt(e.target.value) || 1))}
            />
            <span style={{ fontSize: '0.88rem', color: '#cbd5e1', fontWeight: '600' }}>ngày</span>
          </div>

          <div style={{ fontSize: '0.86rem', color: '#94a3b8' }}>
            Dự kiến đưa <span style={{ color: '#38bdf8', fontWeight: '700' }}>{daysAhead * (settings.daily_quota || 2)} video</span> tiếp theo lên Meta Cloud Schedule (Quota: {settings.daily_quota || 2} video/ngày).
          </div>

          <div style={{ display: 'flex', gap: '8px' }}>
            {preScheduleTask.status === 'running' || preScheduleTask.status === 'starting' ? (
              <button className="fb-btn fb-btn-danger" onClick={handleCancelPreSchedule}>
                🛑 Dừng Tác Vụ
              </button>
            ) : (
              <button className="fb-btn fb-btn-primary" onClick={handleStartPreSchedule}>
                🚀 Bắt Đầu Đặt Lịch Đám Mây
              </button>
            )}
          </div>
        </div>

        {/* Live Progress Box */}
        {(preScheduleTask.status === 'running' || preScheduleTask.status === 'starting' || preScheduleTask.status === 'completed' || preScheduleTask.status === 'canceled' || preScheduleTask.status === 'error') && (
          <div className="fb-progress-box">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.84rem' }}>
              <span style={{
                color: preScheduleTask.status === 'error' ? '#fca5a5' : '#f8fafc',
                fontWeight: '600'
              }}>
                {preScheduleTask.status === 'running'
                  ? `⏳ Đang xử lý video ${preScheduleTask.current_index}/${preScheduleTask.total_items}: "${preScheduleTask.current_video_title}"`
                  : (preScheduleTask.message || preScheduleTask.error || 'Trạng thái hoàn tất')}
              </span>
              <span style={{ color: preScheduleTask.status === 'error' ? '#f87171' : '#38bdf8', fontWeight: '700' }}>
                {preScheduleTask.progress_percent || 0}%
              </span>
            </div>
            {preScheduleTask.error && (
              <div style={{ color: '#fca5a5', fontSize: '0.8rem', background: 'rgba(239,68,68,0.1)', padding: '6px 10px', borderRadius: '6px', border: '1px solid rgba(239,68,68,0.25)' }}>
                ⚠️ <strong>Chi tiết:</strong> {preScheduleTask.error}
              </div>
            )}
            <div className="fb-progress-bar-bg">
              <div
                className="fb-progress-bar-fill"
                style={{
                  width: `${preScheduleTask.progress_percent || 0}%`,
                  background: preScheduleTask.status === 'error' ? '#ef4444' : undefined
                }}
              ></div>
            </div>
          </div>
        )}
      </div>

      {/* Next Scheduled Post Notice */}
      {stats.next_scheduled && (
        <div style={{
          background: 'rgba(56, 189, 248, 0.08)',
          border: '1px solid rgba(56, 189, 248, 0.25)',
          borderRadius: '10px',
          padding: '12px 18px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          fontSize: '0.88rem'
        }}>
          <div>
            <span style={{ color: '#38bdf8', fontWeight: '700' }}>⏰ Video tiếp theo sẽ xuất bản lúc: </span>
            <span style={{ color: '#f8fafc', fontWeight: '600' }}>{formatTimestamp(stats.next_scheduled.scheduled_publish_time)}</span>
            <span style={{ color: '#94a3b8', marginLeft: '12px' }}>— "{stats.next_scheduled.fb_title || stats.next_scheduled.original_title}"</span>
          </div>
          <button
            className="fb-btn fb-btn-primary fb-btn-sm"
            onClick={() => handlePublishNow(stats.next_scheduled.id)}
            disabled={isPublishingId === stats.next_scheduled.id}
          >
            {isPublishingId === stats.next_scheduled.id ? 'Đang đăng...' : '🚀 Đăng Ngay Bây Giờ'}
          </button>
        </div>
      )}

      {/* Settings & Automation Accordion */}
      <div className="fb-settings-accordion">
        <div className="fb-settings-header" onClick={() => setIsSettingsOpen(!isSettingsOpen)}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: '600' }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
            Cài đặt Lịch Đăng, Lead-Time Buffer & Khung Mẫu Bài Viết (Template)
          </div>
          <span>{isSettingsOpen ? '▲ Thu gọn' : '▼ Mở rộng cài đặt'}</span>
        </div>

        {isSettingsOpen && (
          <div className="fb-settings-body">
            {/* Row 1: Quota & Lead-Time Buffer */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
              <div className="fb-form-group">
                <label>Số lượng video đăng mỗi ngày</label>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <input
                    type="range"
                    min="1"
                    max="10"
                    value={settings.daily_quota}
                    onChange={(e) => setSettings(prev => ({ ...prev, daily_quota: parseInt(e.target.value) || 1 }))}
                    style={{ flex: 1 }}
                  />
                  <span style={{ fontWeight: '700', fontSize: '1.1rem', color: '#38bdf8', minWidth: '70px' }}>
                    {settings.daily_quota} video/ngày
                  </span>
                </div>
              </div>

              <div className="fb-form-group">
                <label>
                  <span>Thời gian đệm chuẩn bị (Lead-Time Buffer)</span>
                  <span style={{ fontSize: '0.75rem', color: '#38bdf8' }}>Khuyến nghị 60 phút cho video &gt;1h</span>
                </label>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <input
                    type="range"
                    min="30"
                    max="120"
                    step="10"
                    value={settings.lead_time_minutes || 60}
                    onChange={(e) => setSettings(prev => ({ ...prev, lead_time_minutes: parseInt(e.target.value) || 60 }))}
                    style={{ flex: 1 }}
                  />
                  <span style={{ fontWeight: '700', fontSize: '1.1rem', color: '#a855f7', minWidth: '70px' }}>
                    {settings.lead_time_minutes || 60} phút
                  </span>
                </div>
              </div>
            </div>

            {/* Row 2: Sort Order & Time Slots */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
              <div className="fb-form-group">
                <label>Thứ tự ưu tiên hàng đợi (Sort Order)</label>
                <select
                  className="fb-select"
                  value={settings.sort_order_mode}
                  onChange={(e) => setSettings(prev => ({ ...prev, sort_order_mode: e.target.value }))}
                >
                  <option value="oldest_first">Từ cũ nhất ➔ Mới nhất (Oldest First - Mặc định)</option>
                  <option value="newest_first">Từ mới nhất ➔ Cũ nhất (Newest First)</option>
                </select>
              </div>

              <div className="fb-form-group">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <label style={{ margin: 0 }}>Các khung giờ đăng trong ngày</label>
                  <span style={{ fontSize: '0.78rem', color: '#94a3b8' }}>
                    {(settings.schedule_times || []).length} khung giờ
                  </span>
                </div>
                <div className="fb-chips-container" style={{ minHeight: '38px', alignItems: 'center' }}>
                  {(settings.schedule_times || []).map((slot) => (
                    <span key={slot} className="fb-chip">
                      🕒 {slot}
                      <span
                        className="fb-chip-remove"
                        onClick={() => handleRemoveTimeSlot(slot)}
                        title={`Xóa khung giờ ${slot}`}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleRemoveTimeSlot(slot) }}
                      >
                        ✕
                      </span>
                    </span>
                  ))}

                  {isAddingTime ? (
                    <div className="fb-time-adder-box">
                      <input
                        type="time"
                        className="fb-time-input-sm"
                        value={newTimeSlot}
                        onChange={(e) => setNewTimeSlot(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault()
                            handleAddTimeSlot()
                          } else if (e.key === 'Escape') {
                            e.preventDefault()
                            handleCancelAddTime()
                          }
                        }}
                        autoFocus
                      />
                      <button
                        type="button"
                        className="fb-btn fb-btn-primary fb-btn-sm"
                        onClick={() => handleAddTimeSlot()}
                        title="Thêm khung giờ này"
                      >
                        ✓ Thêm
                      </button>
                      <button
                        type="button"
                        className="fb-btn fb-btn-secondary fb-btn-sm"
                        onClick={handleCancelAddTime}
                        title="Hủy thêm giờ"
                      >
                        ✕ Hủy
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="fb-btn fb-btn-secondary fb-btn-sm"
                      onClick={() => {
                        setIsAddingTime(true)
                        setNewTimeSlot('')
                      }}
                    >
                      + Thêm giờ
                    </button>
                  )}

                  {(!settings.schedule_times || settings.schedule_times.length === 0) && !isAddingTime && (
                    <span style={{ fontSize: '0.8rem', color: '#64748b', fontStyle: 'italic' }}>
                      Chưa có khung giờ nào. Bấm "+ Thêm giờ" để tạo.
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Row 3: Auto-Sync & Auto-Publish */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', background: 'rgba(0,0,0,0.2)', padding: '14px', borderRadius: '8px' }}>
              <div className="fb-form-group">
                <label style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <input
                    type="checkbox"
                    checked={settings.auto_sync_enabled}
                    onChange={(e) => setSettings(prev => ({ ...prev, auto_sync_enabled: e.target.checked }))}
                  />
                  <span>Tự động quét kênh YouTube (Auto-Sync)</span>
                </label>
                {settings.auto_sync_enabled && (
                  <div style={{ display: 'flex', gap: '10px', marginTop: '6px', alignItems: 'center' }}>
                    <select
                      className="fb-select"
                      style={{ width: '160px' }}
                      value={settings.auto_sync_type}
                      onChange={(e) => setSettings(prev => ({ ...prev, auto_sync_type: e.target.value }))}
                    >
                      <option value="interval">Theo tần suất</option>
                      <option value="fixed_time">Giờ cố định</option>
                    </select>
                    {settings.auto_sync_type === 'interval' ? (
                      <select
                        className="fb-select"
                        value={settings.auto_sync_interval_hours}
                        onChange={(e) => setSettings(prev => ({ ...prev, auto_sync_interval_hours: parseInt(e.target.value) || 6 }))}
                      >
                        <option value="2">Mỗi 2 giờ</option>
                        <option value="4">Mỗi 4 giờ</option>
                        <option value="6">Mỗi 6 giờ</option>
                        <option value="12">Mỗi 12 giờ</option>
                        <option value="24">Mỗi 24 giờ</option>
                      </select>
                    ) : (
                      <input
                        type="text"
                        className="fb-input"
                        placeholder="06:00, 18:00"
                        value={settings.auto_sync_fixed_times?.join(', ') || ''}
                        onChange={(e) => setSettings(prev => ({
                          ...prev,
                          auto_sync_fixed_times: e.target.value.split(',').map(s => s.trim()).filter(Boolean)
                        }))}
                      />
                    )}
                  </div>
                )}
                {settings.last_synced_at && (
                  <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
                    Lần quét gần nhất: {formatTimestamp(Math.floor(new Date(settings.last_synced_at).getTime() / 1000))}
                  </span>
                )}
              </div>

              <div className="fb-form-group">
                <label style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <input
                    type="checkbox"
                    checked={settings.auto_publish_enabled}
                    onChange={(e) => setSettings(prev => ({ ...prev, auto_publish_enabled: e.target.checked }))}
                  />
                  <span>Tự động đăng khi đến giờ (Auto-Publish Worker)</span>
                </label>
                <span style={{ fontSize: '0.78rem', color: '#94a3b8' }}>
                  Khi bật, worker ngầm sẽ tự động kích hoạt trước {settings.lead_time_minutes || 60} phút để tải và upload video chuẩn xác lên Meta Cloud.
                </span>
              </div>
            </div>

            {/* Row 4: Vertical Media & Default Tags */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
              <div className="fb-form-group" style={{ background: 'rgba(56, 189, 248, 0.06)', padding: '14px', borderRadius: '8px' }}>
                <label style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <input
                    type="checkbox"
                    checked={Boolean(settings.convert_to_vertical)}
                    onChange={(e) => setSettings(prev => ({ ...prev, convert_to_vertical: e.target.checked }))}
                  />
                  <span>Chuyển video & thumbnail sang 9:16 – nền mờ</span>
                </label>
                <span style={{ fontSize: '0.78rem', color: '#94a3b8' }}>
                  Khi bật, file được chuyển thành 1080×1920 trước khi upload. Nội dung 16:9 được giữ trọn ở giữa và không bị crop.
                </span>
              </div>

              <div className="fb-form-group">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <label>Tag mặc định của Fanpage</label>
                  <span style={{ fontSize: '0.75rem', color: '#38bdf8' }}>
                    {normalizeDefaultTags(defaultTagsDraft).length} tag
                  </span>
                </div>
                <textarea
                  className="fb-textarea"
                  rows="3"
                  value={defaultTagsDraft}
                  onChange={(e) => setDefaultTagsDraft(e.target.value)}
                  placeholder="Ví dụ: Tên thương hiệu, Lịch sử Việt Nam, Kiến thức"
                />
                <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
                  Phân cách bằng dấu phẩy hoặc xuống dòng. Tag mặc định được ưu tiên trước; tối đa 5 hashtag caption, 8 custom labels và 10 Meta content tags.
                </span>
              </div>
            </div>

            {/* Row 5: Post Template */}
            <div className="fb-form-group">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <label>Khung mẫu bài đăng (Post Template)</label>
                <div style={{ display: 'flex', gap: '6px' }}>
                  <button className="fb-btn fb-btn-secondary fb-btn-sm" onClick={() => handleInsertTag('{title}')}>+ {'{title}'}</button>
                  <button className="fb-btn fb-btn-secondary fb-btn-sm" onClick={() => handleInsertTag('{clean_description}')}>+ {'{clean_description}'}</button>
                  <button className="fb-btn fb-btn-secondary fb-btn-sm" onClick={() => handleInsertTag('{hashtags}')}>+ {'{hashtags}'}</button>
                </div>
              </div>
              <textarea
                className="fb-textarea"
                rows="4"
                value={settings.post_template}
                onChange={(e) => setSettings(prev => ({ ...prev, post_template: e.target.value }))}
                placeholder="Nhập khung mẫu bài đăng..."
              />
            </div>

            {/* Save Button */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
              <button className="fb-btn fb-btn-success" onClick={handleSaveSettings}>
                💾 Lưu Cấu Hình Fanpage
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Queue Filter & Search Bar */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px' }}>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{ fontSize: '0.85rem', color: '#94a3b8', fontWeight: '600' }}>Lọc:</span>
          {['all', 'pending', 'scheduled', 'published', 'skipped', 'error'].map((st) => (
            <button
              key={st}
              className={`fb-btn fb-btn-sm ${statusFilter === st ? 'fb-btn-primary' : 'fb-btn-secondary'}`}
              onClick={() => { setStatusFilter(st); setPage(1) }}
            >
              {st === 'all' ? 'Tất cả' : st === 'pending' ? 'Chờ đăng' : st === 'scheduled' ? 'Đã lên lịch' : st === 'published' ? 'Đã đăng' : st === 'skipped' ? 'Bỏ qua' : 'Lỗi'}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: '10px' }}>
          <input
            type="search"
            className="fb-input"
            style={{ width: '260px' }}
            placeholder="🔎 Tìm tiêu đề hoặc Video ID..."
            value={searchQuery}
            onChange={(e) => { setSearchQuery(e.target.value); setPage(1) }}
          />
        </div>
      </div>

      {/* Queue Table */}
      <div className="fb-table-container">
        <table className="fb-table">
          <thead>
            <tr>
              <th style={{ width: '40px' }}>#</th>
              <th style={{ width: '80px' }}>Ảnh bìa</th>
              <th>Tiêu đề Video</th>
              <th style={{ width: '120px' }}>Ngày YouTube</th>
              <th style={{ width: '150px' }}>Lịch xuất bản</th>
              <th style={{ width: '110px' }}>Trạng thái</th>
              <th style={{ width: '220px', textAlign: 'right' }}>Thao tác</th>
            </tr>
          </thead>
          <tbody>
            {queueItems.length === 0 ? (
              <tr>
                <td colSpan="7" style={{ textAlign: 'center', padding: '36px', color: '#94a3b8' }}>
                  {isSyncing ? 'Đang quét dữ liệu từ YouTube...' : 'Không có video nào trong hàng đợi của Fanpage này. Nhấn [Quét Kênh YouTube] ở trên để nạp video.'}
                </td>
              </tr>
            ) : (
              queueItems.map((item, idx) => (
                <tr key={item.id}>
                  <td style={{ color: '#94a3b8', fontWeight: '600' }}>
                    {item.sort_order || ((page - 1) * pageSize + idx + 1)}
                  </td>
                  <td>
                    <img
                      src={item.thumbnail_url || `https://i.ytimg.com/vi/${item.youtube_id}/default.jpg`}
                      alt={item.original_title}
                      className="fb-thumb"
                      onError={(e) => { e.target.src = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="72" height="40" fill="%23334155"><rect width="100%" height="100%"/></svg>' }}
                    />
                  </td>
                  <td>
                    <div style={{ fontWeight: '600', color: '#f8fafc', marginBottom: '2px', display: 'flex', alignItems: 'center' }}>
                      <span>{item.fb_title || item.original_title}</span>
                      {item.file_size_bytes > 0 && (
                        <span className="badge-size">💾 {formatBytes(item.file_size_bytes)}</span>
                      )}
                    </div>
                    <div style={{ fontSize: '0.75rem', color: '#64748b' }}>
                      ID: <a href={item.youtube_url} target="_blank" rel="noreferrer" style={{ color: '#38bdf8' }}>{item.youtube_id}</a>
                      {item.fb_post_id && (
                        <span style={{ marginLeft: '10px', color: '#4ade80' }}>
                          Post ID: {item.fb_post_id}
                        </span>
                      )}
                    </div>
                    {item.error_message && (
                      <div style={{ fontSize: '0.75rem', color: '#f87171', marginTop: '2px' }}>
                        ⚠️ {item.error_message}
                      </div>
                    )}
                  </td>
                  <td style={{ color: '#cbd5e1', whiteSpace: 'nowrap' }}>
                    {formatDate(item.youtube_upload_date)}
                  </td>
                  <td style={{ color: '#38bdf8', fontWeight: '500', whiteSpace: 'nowrap' }}>
                    {formatTimestamp(item.scheduled_publish_time)}
                  </td>
                  <td>
                    <span className={`badge-status ${item.status}`}>
                      {item.status === 'published' ? 'Đã đăng' : item.status === 'scheduled' ? 'Đã lên lịch' : item.status === 'downloading' ? 'Đang tải MP4' : item.status === 'uploading' ? 'Đang xuất bản' : item.status === 'pending' ? 'Chờ tới lượt' : item.status === 'skipped' ? 'Bỏ qua' : 'Lỗi'}
                    </span>
                  </td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <div style={{ display: 'inline-flex', gap: '6px' }}>
                      {item.status !== 'published' && (
                        <button
                          className="fb-btn fb-btn-primary fb-btn-sm"
                          onClick={() => handlePublishNow(item.id)}
                          disabled={isPublishingId === item.id}
                          title="Tải và đăng ngay video này"
                        >
                          {isPublishingId === item.id ? '...' : 'Đăng ngay'}
                        </button>
                      )}
                      <button
                        className="fb-btn fb-btn-secondary fb-btn-sm"
                        onClick={() => handleOpenEdit(item)}
                        title="Chỉnh sửa tiêu đề/caption"
                      >
                        Sửa
                      </button>
                      <button
                        className={`fb-btn fb-btn-sm ${item.status === 'skipped' ? 'fb-btn-warning' : 'fb-btn-secondary'}`}
                        onClick={() => handleToggleSkip(item)}
                        title={item.status === 'skipped' ? 'Đưa lại vào hàng đợi' : 'Bỏ qua video này'}
                      >
                        {item.status === 'skipped' ? 'Khôi phục' : 'Bỏ qua'}
                      </button>
                      <button
                        className="fb-btn fb-btn-danger fb-btn-sm"
                        onClick={() => handleDeleteItem(item.id)}
                        title="Xóa khỏi hàng đợi"
                      >
                        ✕
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination Controls */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '10px' }}>
          <span style={{ fontSize: '0.85rem', color: '#94a3b8' }}>
            Hiển thị {queueItems.length} trên tổng số {queueTotal} video (Trang {page} / {totalPages})
          </span>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              className="fb-btn fb-btn-secondary fb-btn-sm"
              disabled={page <= 1}
              onClick={() => setPage(prev => Math.max(1, prev - 1))}
            >
              ◀ Trang trước
            </button>
            <button
              className="fb-btn fb-btn-secondary fb-btn-sm"
              disabled={page >= totalPages}
              onClick={() => setPage(prev => Math.min(totalPages, prev + 1))}
            >
              Trang sau ▶
            </button>
          </div>
        </div>
      )}

      {/* Edit Video Modal */}
      {editingItem && (
        <div className="fb-modal-backdrop" onClick={() => setEditingItem(null)}>
          <div className="fb-modal" onClick={(e) => e.stopPropagation()}>
            <div className="fb-modal-header">
              <h3 style={{ margin: 0, fontSize: '1.1rem', color: '#f8fafc' }}>
                ✏️ Chỉnh sửa Video #{editingItem.id} ({editingItem.youtube_id})
              </h3>
              <button
                onClick={() => setEditingItem(null)}
                style={{ background: 'none', border: 'none', color: '#94a3b8', fontSize: '1.2rem', cursor: 'pointer' }}
              >✕</button>
            </div>
            <div className="fb-modal-body">
              <div className="fb-form-group">
                <label>Tiêu đề xuất bản</label>
                <input
                  type="text"
                  className="fb-input"
                  value={editForm.fb_title}
                  onChange={(e) => setEditForm(prev => ({ ...prev, fb_title: e.target.value }))}
                />
              </div>

              <div className="fb-form-group">
                <label>Nội dung Caption (Mô tả bài viết & Hashtags)</label>
                <textarea
                  className="fb-textarea"
                  rows="8"
                  value={editForm.fb_description}
                  onChange={(e) => setEditForm(prev => ({ ...prev, fb_description: e.target.value }))}
                />
              </div>

              <div className="fb-form-group">
                <label>Thời gian xuất bản dự kiến (Local Datetime)</label>
                <input
                  type="datetime-local"
                  className="fb-input"
                  value={editForm.scheduled_datetime_local}
                  onChange={(e) => setEditForm(prev => ({ ...prev, scheduled_datetime_local: e.target.value }))}
                />
              </div>
            </div>
            <div className="fb-modal-footer">
              <button className="fb-btn fb-btn-secondary" onClick={() => setEditingItem(null)}>Hủy</button>
              <button className="fb-btn fb-btn-success" onClick={handleSaveEdit}>Lưu Thay Đổi</button>
            </div>
          </div>
        </div>
      )}

      {/* New Campaign Modal */}
      {isNewCampaignModalOpen && (
        <div className="fb-modal-backdrop" onClick={() => setIsNewCampaignModalOpen(false)}>
          <div className="fb-modal" onClick={(e) => e.stopPropagation()}>
            <div className="fb-modal-header">
              <h3 style={{ margin: 0, fontSize: '1.1rem', color: '#f8fafc' }}>
                ➕ Thêm Chiến Dịch Fanpage Mới
              </h3>
              <button
                onClick={() => setIsNewCampaignModalOpen(false)}
                style={{ background: 'none', border: 'none', color: '#94a3b8', fontSize: '1.2rem', cursor: 'pointer' }}
              >✕</button>
            </div>
            <div className="fb-modal-body">
              <div className="fb-form-group">
                <label>Tên Fanpage (Gợi nhớ)</label>
                <input
                  type="text"
                  className="fb-input"
                  placeholder="VD: Fanpage Tin Tức AI, Fanpage Truyện Ma..."
                  value={newCampaignForm.page_name}
                  onChange={(e) => setNewCampaignForm(prev => ({ ...prev, page_name: e.target.value }))}
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div className="fb-form-group">
                  <label>Fanpage Page ID</label>
                  <input
                    type="text"
                    className="fb-input"
                    placeholder="VD: 100085948392019"
                    value={newCampaignForm.page_id}
                    onChange={(e) => setNewCampaignForm(prev => ({ ...prev, page_id: e.target.value }))}
                  />
                </div>
                <div className="fb-form-group">
                  <label>Page Access Token</label>
                  <input
                    type="password"
                    className="fb-input"
                    placeholder="EAAG..."
                    value={newCampaignForm.access_token}
                    onChange={(e) => setNewCampaignForm(prev => ({ ...prev, access_token: e.target.value }))}
                  />
                </div>
              </div>

              <div className="fb-form-group">
                <label>GPM Profile Đăng bài (Tùy chọn - Định tuyến Proxy)</label>
                <select
                  className="fb-select"
                  value={newCampaignForm.gpm_profile_id}
                  onChange={(e) => setNewCampaignForm(prev => ({ ...prev, gpm_profile_id: e.target.value }))}
                >
                  <option value="">💻 Direct Local IP (Không dùng GPM Proxy)</option>
                  {Array.isArray(gpmProfiles) && gpmProfiles.map(p => (
                    <option key={p.id} value={p.id}>
                      🔒 [GPM] {p.name || p.id} {p.raw_proxy ? `(${p.raw_proxy})` : ''}
                    </option>
                  ))}
                </select>
              </div>

              <div className="fb-form-group">
                <label>Kênh YouTube Nguồn</label>
                <input
                  type="text"
                  className="fb-input"
                  placeholder="https://www.youtube.com/@ChannelName"
                  value={newCampaignForm.source_channel_id}
                  onChange={(e) => setNewCampaignForm(prev => ({ ...prev, source_channel_id: e.target.value }))}
                />
              </div>
            </div>
            <div className="fb-modal-footer">
              <button className="fb-btn fb-btn-secondary" onClick={() => setIsNewCampaignModalOpen(false)}>Hủy</button>
              <button className="fb-btn fb-btn-primary" onClick={handleCreateNewCampaign}>Tạo Chiến Dịch</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
