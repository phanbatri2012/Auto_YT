import { useCallback, useEffect, useState } from 'react'

const API_BASE = 'http://127.0.0.1:8080'
const WEEKDAYS = ['Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy', 'Chủ Nhật']

export default function YouTubeChannelSettings({
  onChannelsChange,
  promptVersions = {},
  activePromptVersion = ''
}) {
  const [config, setConfig] = useState({
    client_id: '', client_name: '', client_secret: '',
    redirect_uri: `${API_BASE}/api/youtube-comments/oauth/callback`,
    client_secret_configured: false
  })
  const [oauthConfigs, setOauthConfigs] = useState([])
  const [channels, setChannels] = useState([])
  const [selectedPromptVersion, setSelectedPromptVersion] = useState(activePromptVersion)
  const [selectedChannelIdOverride, setSelectedChannelIdOverride] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  // GPM-Login v3 Local API States
  const [gpmConfig, setGpmConfig] = useState({
    api_url: 'http://127.0.0.1:19995',
    auto_stop_on_finish: true,
    timeout_seconds: 15
  })
  const [gpmStatus, setGpmStatus] = useState({ online: false, total_profiles: 0, message: '' })
  const [gpmProfiles, setGpmProfiles] = useState([])
  const [gpmBusy, setGpmBusy] = useState(false)
  const [gpmSearchFilter, setGpmSearchFilter] = useState('')
  const [newChannelGpmProfileId, setNewChannelGpmProfileId] = useState('')

  const promptEntries = Object.entries(promptVersions)
  const selectedPrompt = promptVersions[selectedPromptVersion] || null
  const promptDefaultChannelId = String(
    selectedPrompt?.default_youtube_channel_id || ''
  ).trim()

  const effectiveChannelId = selectedChannelIdOverride || promptDefaultChannelId || (channels[0]?.channel_id || '')
  const selectedChannel = channels.find(
    channel => String(channel.channel_id || '') === effectiveChannelId
  ) || null

  useEffect(() => {
    setSelectedPromptVersion(previous => {
      if (activePromptVersion && promptVersions[activePromptVersion]) {
        return activePromptVersion
      }
      return promptVersions[previous] ? previous : (Object.keys(promptVersions)[0] || '')
    })
  }, [activePromptVersion, promptVersions])

  const loadGpmProfilesList = useCallback(async (customApiUrl = '') => {
    try {
      const url = customApiUrl || gpmConfig.api_url
      const query = new URLSearchParams({ api_url: url, page_size: '300' })
      const profilesRes = await fetch(`${API_BASE}/api/gpm/profiles?${query.toString()}`)
      if (profilesRes.ok) {
        const profilesData = await profilesRes.json()
        const items = Array.isArray(profilesData.items) ? profilesData.items : []
        setGpmProfiles(items)
        return items
      }
    } catch (error) {
      console.warn('Lỗi tải danh sách profiles GPM:', error)
    }
    return []
  }, [gpmConfig.api_url])

  const loadGpm = useCallback(async (isFocus = false) => {
    try {
      const [configRes, statusRes] = await Promise.all([
        fetch(`${API_BASE}/api/gpm/config`),
        fetch(`${API_BASE}/api/gpm/status`)
      ])
      const configData = await configRes.json()
      const statusData = await statusRes.json()
      if (!isFocus) {
        setGpmConfig(configData)
      }
      setGpmStatus(statusData)

      if (statusData.online) {
        await loadGpmProfilesList(configData.api_url)
      }
    } catch (error) {
      console.warn('Lỗi load GPM:', error)
    }
  }, [loadGpmProfilesList])

  const load = useCallback(async ({ isFocus = false } = {}) => {
    try {
      const [configResponse, channelsResponse] = await Promise.all([
        fetch(`${API_BASE}/api/youtube-comments/oauth/config`),
        fetch(`${API_BASE}/api/youtube-comments/channels`)
      ])
      const configData = await configResponse.json()
      const channelData = await channelsResponse.json()
      if (!isFocus) {
        setConfig(previous => ({
          ...previous,
          ...configData,
          client_secret: ''
        }))
      } else {
        setConfig(previous => ({
          ...previous,
          redirect_uri: configData.redirect_uri || previous.redirect_uri,
          client_id: previous.client_id || configData.client_id || '',
          client_name: previous.client_name || configData.client_name || '',
          client_secret: previous.client_secret,
          client_secret_configured: configData.client_secret_configured ?? previous.client_secret_configured
        }))
      }
      setOauthConfigs(Array.isArray(configData.items) ? configData.items : [])
      const items = Array.isArray(channelData.items) ? channelData.items : []
      const defaultClientId = configData.client_id || (Array.isArray(configData.items) && configData.items[0]?.client_id) || ''
      setChannels(items.map(channel => ({
        ...channel,
        oauth_client_choice: channel.oauth_client_id || defaultClientId
      })))
      onChannelsChange?.(items)
      await loadGpm(isFocus)
    } catch (error) {
      if (!isFocus) {
        setMessage(`❌ Không thể đọc cấu hình YouTube: ${error.message}`)
      }
    }
  }, [onChannelsChange, loadGpm])

  useEffect(() => {
    load()
    const onFocus = () => load({ isFocus: true })
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [load])

  const checkGpmConnection = async () => {
    setGpmBusy(true)
    try {
      const query = new URLSearchParams({ api_url: gpmConfig.api_url })
      const res = await fetch(`${API_BASE}/api/gpm/status?${query.toString()}`)
      const data = await res.json()
      setGpmStatus(data)
      if (data.online) {
        const loadedItems = await loadGpmProfilesList(gpmConfig.api_url)
        setMessage(`✅ Kết nối GPM-Login thành công! Tìm thấy ${loadedItems.length || data.total_profiles} profiles.`)
      } else {
        setMessage(`⚠️ Không kết nối được GPM: ${data.message}`)
      }
    } catch (error) {
      setMessage(`❌ Lỗi kết nối GPM: ${error.message}`)
    } finally {
      setGpmBusy(false)
    }
  }

  const saveGpmConfigHandler = async () => {
    setGpmBusy(true)
    try {
      const res = await fetch(`${API_BASE}/api/gpm/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(gpmConfig)
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setGpmConfig(data)
      await checkGpmConnection()
      setMessage('✅ Đã lưu cấu hình GPM Local API.')
    } catch (error) {
      setMessage(`❌ ${error.message}`)
    } finally {
      setGpmBusy(false)
    }
  }

  const startGpmProfileHandler = async (profileId) => {
    if (!profileId) return
    setGpmBusy(true)
    try {
      const res = await fetch(`${API_BASE}/api/gpm/profiles/${encodeURIComponent(profileId)}/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(`🚀 Đã mở GPM Profile thành công. Bạn có thể thao tác trên trình duyệt.`)
    } catch (error) {
      setMessage(`❌ Không thể mở Profile GPM: ${error.message}`)
    } finally {
      setGpmBusy(false)
    }
  }

  const stopGpmProfileHandler = async (profileId) => {
    if (!profileId) return
    setGpmBusy(true)
    try {
      const res = await fetch(`${API_BASE}/api/gpm/profiles/${encodeURIComponent(profileId)}/stop`, {
        method: 'POST'
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(`⏹ Đã đóng GPM Profile.`)
    } catch (error) {
      setMessage(`❌ Lỗi khi đóng Profile GPM: ${error.message}`)
    } finally {
      setGpmBusy(false)
    }
  }

  const openStudioHandler = async (channelId) => {
    if (!channelId) return
    setGpmBusy(true)
    try {
      const res = await fetch(`${API_BASE}/api/youtube-comments/channels/${channelId}/open-studio`, {
        method: 'POST'
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(`🚀 Đã mở YouTube Studio của kênh '${data.channel_title || ''}' trong GPM Profile.`)
    } catch (error) {
      setMessage(`❌ Lỗi khi mở Studio qua GPM: ${error.message}`)
    } finally {
      setGpmBusy(false)
    }
  }

  const verifyStudioHandler = async (channelId) => {
    if (!channelId) return
    setGpmBusy(true)
    try {
      const res = await fetch(`${API_BASE}/api/youtube-comments/channels/${channelId}/verify-gpm-studio`, {
        method: 'POST'
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      if (data.logged_in) {
        setMessage(`🟢 Profile GPM đã đăng nhập YouTube Studio thành công! (Kênh: ${data.channel_title})`)
      } else {
        setMessage(`🟡 ${data.message || 'Chưa đăng nhập Google trong Profile này. Vui lòng mở Profile trên GPM để đăng nhập.'}`)
      }
    } catch (error) {
      setMessage(`❌ Lỗi kiểm tra đăng nhập Studio: ${error.message}`)
    } finally {
      setGpmBusy(false)
    }
  }

  const selectOauthConfig = clientId => {
    const selected = oauthConfigs.find(item => item.client_id === clientId)
    if (!selected) {
      setConfig({
        client_id: '',
        client_name: '',
        client_secret: '',
        redirect_uri: `${API_BASE}/api/youtube-comments/oauth/callback`,
        client_secret_configured: false
      })
      return
    }
    setConfig({
      ...selected,
      client_secret: ''
    })
  }

  const saveConfig = async () => {
    setBusy(true)
    try {
      const response = await fetch(`${API_BASE}/api/youtube-comments/oauth/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      setConfig(previous => ({ ...previous, ...data, client_secret: '' }))
      await load()
      setMessage('✅ Đã lưu OAuth Client mà không thay đổi các Client của kênh khác.')
    } catch (error) {
      setMessage(`❌ ${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  const connectChannel = async ({ clientId = '', expectedChannel = null, gpmProfileId = '' } = {}) => {
    const selectedClientId = String(
      clientId ||
      expectedChannel?.oauth_client_choice ||
      config.client_id ||
      (oauthConfigs[0]?.client_id || '')
    ).trim()

    if (!selectedClientId) {
      setMessage('❌ Vui lòng chọn hoặc cấu hình OAuth Client (Google Client ID) trước khi kết nối.')
      return
    }

    const targetGpm = expectedChannel?.gpm_profile_id || gpmProfileId
    setBusy(true)
    setMessage(
      targetGpm
        ? `⏳ Đang kết nối tới Profile GPM (${expectedChannel?.gpm_profile_name || targetGpm}) để mở trang xác thực Google...`
        : '⏳ Đang khởi tạo phiên xác thực Google OAuth...'
    )
    try {
      const query = new URLSearchParams({ client_id: selectedClientId })
      if (expectedChannel?.channel_id) {
        query.set('expected_channel_id', expectedChannel.channel_id)
      }
      if (targetGpm) {
        query.set('gpm_profile_id', targetGpm)
      }
      const response = await fetch(`${API_BASE}/api/youtube-comments/oauth/start?${query.toString()}`, {
        method: 'POST'
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)

      const targetGpmProfileId = targetGpm || data.gpm_profile_id

      if (targetGpmProfileId) {
        try {
          const gpmRes = await fetch(`${API_BASE}/api/gpm/profiles/${encodeURIComponent(targetGpmProfileId)}/open-url`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: data.authorization_url })
          })
          const gpmData = await gpmRes.json()
          if (!gpmRes.ok) throw new Error(gpmData.detail || `HTTP ${gpmRes.status}`)
          setMessage(
            `🚀 Đã mở trang cấp quyền Google trong cửa sổ Profile GPM (${expectedChannel?.gpm_profile_name || targetGpmProfileId}). Tên hiện trên màn hình Google là tên ứng dụng OAuth, không phải tên kênh. Vui lòng hoàn tất xác thực trên trình duyệt GPM. Danh sách sẽ tự cập nhật khi bạn quay lại.`
          )
          return
        } catch (gpmErr) {
          console.error('Không thể mở tự động trong GPM:', gpmErr)
          setMessage(
            `❌ Không thể mở trang xác thực Google trong Profile GPM (${expectedChannel?.gpm_profile_name || targetGpmProfileId}): ${gpmErr.message}. Vui lòng kiểm tra ứng dụng GPM-Login đang chạy và bấm nút "🚀 Mở Profile" để thử lại.`
          )
          return
        }
      }

      setMessage(
        expectedChannel
          ? `⚠️ Kênh "${expectedChannel.title}" chưa được gán GPM Profile. Vui lòng chọn Profile GPM cho kênh ở mục "GPM Profile liên kết" bên dưới trước khi kết nối OAuth để bảo vệ 100% tài khoản và proxy.`
          : '⚠️ Vui lòng chọn Profile GPM cho kênh trước khi kết nối OAuth để đảm bảo cách ly tài khoản và bảo mật.'
      )
    } catch (error) {
      setMessage(`❌ ${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  const changeChannel = (id, field, value) => {
    setChannels(previous => previous.map(channel => (
      channel.id === id ? { ...channel, [field]: value } : channel
    )))
  }

  const handleSelectGpmProfile = (channelId, profileId) => {
    const selected = gpmProfiles.find(p => p.id === profileId)
    changeChannel(channelId, 'gpm_profile_id', profileId)
    changeChannel(channelId, 'gpm_profile_name', selected ? selected.name : '')
    changeChannel(channelId, 'gpm_proxy_display', selected ? (selected.proxy_display || '') : '')
    changeChannel(channelId, 'gpm_proxy_configured', Boolean(selected?.proxy_configured))
  }

  const addPublicationSlot = channel => {
    changeChannel(channel.id, 'publication_slots', [
      ...(Array.isArray(channel.publication_slots) ? channel.publication_slots : []),
      { day: 0, time: '09:00' }
    ])
  }

  const changePublicationSlot = (channel, index, field, value) => {
    const slots = (Array.isArray(channel.publication_slots) ? channel.publication_slots : [])
      .map((slot, slotIndex) => slotIndex === index
        ? { ...slot, [field]: field === 'day' ? Number(value) : value }
        : slot)
    changeChannel(channel.id, 'publication_slots', slots)
  }

  const removePublicationSlot = (channel, index) => {
    const slots = (Array.isArray(channel.publication_slots) ? channel.publication_slots : [])
      .filter((_, slotIndex) => slotIndex !== index)
    changeChannel(channel.id, 'publication_slots', slots)
  }

  const saveChannel = async channel => {
    setBusy(true)
    try {
      const response = await fetch(`${API_BASE}/api/youtube-comments/channels/${channel.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          reply_instruction: channel.reply_instruction || '',
          auto_mode: channel.auto_mode || 'draft_only',
          daily_reply_limit: Number(channel.daily_reply_limit) || 50,
          reply_interval_minutes: Number(channel.reply_interval_minutes) || 5,
          quarter_hour_reply_limit: Number(channel.quarter_hour_reply_limit) || 3,
          hourly_reply_limit: Number(channel.hourly_reply_limit) || 10,
          video_half_hour_reply_limit: Number(channel.video_half_hour_reply_limit) || 3,
          backlog_daily_reply_limit: Number(channel.backlog_daily_reply_limit) || 20,
          reply_window_start: channel.reply_window_start || '08:00',
          reply_window_end: channel.reply_window_end || '22:00',
          reply_paused: Boolean(channel.reply_paused),
          auto_sync: Boolean(channel.auto_sync),
          sync_interval_minutes: Number(channel.sync_interval_minutes) || 10,
          publication_timezone: channel.publication_timezone || 'Asia/Ho_Chi_Minh',
          publication_slots: Array.isArray(channel.publication_slots)
            ? channel.publication_slots
            : [],
          publication_daily_limit: Number(channel.publication_daily_limit) || 1,
          publication_lead_minutes: Number(channel.publication_lead_minutes) || 120,
          publication_paused: Boolean(channel.publication_paused),
          public_upload_verified: Boolean(channel.public_upload_verified),
          gpm_profile_id: channel.gpm_profile_id || '',
          gpm_profile_name: channel.gpm_profile_name || '',
          interaction_mode: channel.interaction_mode || 'gpm_browser',
          auto_heart: channel.auto_heart !== undefined ? Boolean(channel.auto_heart) : true
        })
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      setChannels(previous => previous.map(item => item.id === channel.id
        ? { ...item, ...data, oauth_client_choice: item.oauth_client_choice }
        : item))
      setMessage(`✅ Đã lưu thiết lập và gán Profile GPM cho kênh "${channel.title}".`)
    } catch (error) {
      setMessage(`❌ ${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  const disconnect = async channel => {
    if (!confirm(`Ngắt kết nối ${channel.title}? Các link đã đăng và bình luận đã đồng bộ của kênh này cũng sẽ bị xóa.`)) return
    setBusy(true)
    try {
      const response = await fetch(`${API_BASE}/api/youtube-comments/channels/${channel.id}`, {
        method: 'DELETE'
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      const remainingChannels = channels.filter(item => item.id !== channel.id)
      setChannels(remainingChannels)
      onChannelsChange?.(remainingChannels)
      const clearedCount = Array.isArray(data.cleared_prompt_versions)
        ? data.cleared_prompt_versions.length
        : 0
      setMessage(
        clearedCount
          ? `✅ Đã ngắt kết nối kênh và bỏ liên kết khỏi ${clearedCount} bộ prompt.`
          : '✅ Đã ngắt kết nối kênh.'
      )
    } catch (error) {
      setMessage(`❌ ${error.message}`)
    } finally {
      setBusy(false)
    }
  }

  // Filtered GPM profiles for dropdown / search
  const filteredGpmProfiles = gpmProfiles.filter(p => {
    if (!gpmSearchFilter.trim()) return true
    const term = gpmSearchFilter.toLowerCase()
    return p.name.toLowerCase().includes(term) || (p.proxy_display || '').toLowerCase().includes(term)
  })

  return (
    <div className="prompt-item" style={{ marginBottom: 20 }}>
      {/* Header */}
      <div className="prompt-header">
        <div>
          <label>📺 Kênh YouTube & Cô lập Profile GPM-Login</label>
          <div className="help-text" style={{ marginTop: 5 }}>
            Quản lý độc lập từng kênh YouTube với Proxy tĩnh, Browser Fingerprint và Profile riêng biệt trên GPM-Login, loại bỏ hoàn toàn rủi ro trùng IP khi đăng video, comment hoặc tương tác.
          </div>
        </div>
      </div>

      {/* Global Message Banner */}
      {message && (
        <div style={{
          position: 'sticky',
          top: 10,
          zIndex: 999,
          padding: '12px 18px',
          borderRadius: 8,
          background: message.startsWith('❌') ? 'rgba(239, 68, 68, 0.95)' : message.startsWith('⏳') ? 'rgba(30, 58, 138, 0.95)' : 'rgba(16, 185, 129, 0.95)',
          border: `1px solid ${message.startsWith('❌') ? '#ef4444' : message.startsWith('⏳') ? '#3b82f6' : '#10b981'}`,
          color: '#ffffff',
          fontSize: '0.95rem',
          fontWeight: 600,
          boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backdropFilter: 'blur(8px)',
          margin: '10px 0'
        }}>
          <span>{message}</span>
          <button
            onClick={() => setMessage('')}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#ffffff',
              fontSize: '1.1rem',
              cursor: 'pointer',
              marginLeft: 12
            }}
          >
            ✕
          </button>
        </div>
      )}

      {/* GPM-Login Connection Card */}
      <div className="result-panel" style={{ marginTop: 14, padding: 16, border: '1px solid #2a4365', background: 'rgba(30, 58, 138, 0.18)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: '1.25rem' }}>🛡️</span>
            <strong>Cấu hình GPM-Login v3 Local API</strong>
            <span style={{
              display: 'inline-block',
              padding: '3px 10px',
              borderRadius: 4,
              fontSize: '11px',
              fontWeight: 'bold',
              background: gpmStatus.online ? '#065f46' : '#991b1b',
              color: gpmStatus.online ? '#34d399' : '#fca5a5'
            }}>
              {gpmStatus.online ? `● Online (${gpmProfiles.length || gpmStatus.total_profiles} profiles)` : '○ Offline'}
            </span>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 1fr) auto auto', gap: 10, marginTop: 12 }}>
          <input
            className="version-select"
            value={gpmConfig.api_url || 'http://127.0.0.1:19995'}
            onChange={e => setGpmConfig({ ...gpmConfig, api_url: e.target.value })}
            placeholder="http://127.0.0.1:19995"
            title="Địa chỉ Local API của GPM-Login"
          />
          <button className="btn-secondary" disabled={gpmBusy} onClick={checkGpmConnection}>
            🔍 Kiểm tra kết nối & Tải profiles
          </button>
          <button className="btn-save" disabled={gpmBusy} onClick={saveGpmConfigHandler}>
            💾 Lưu cấu hình GPM
          </button>
        </div>
        <div className="help-text" style={{ marginTop: 6, color: '#93c5fd' }}>
          GPM-Login Local API mặc định chạy ở <code>http://127.0.0.1:19995</code> (kiểm tra trong menu 'Tài liệu API' của GPM-Login).
        </div>
      </div>

      {/* OAuth Client Section */}
      <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
        <label>
          OAuth Client dùng để kết nối kênh
          <select
            className="version-select"
            style={{ width: '100%', marginTop: 6 }}
            value={oauthConfigs.some(item => item.client_id === config.client_id) ? config.client_id : '__new__'}
            onChange={event => selectOauthConfig(event.target.value)}
          >
            {oauthConfigs.map(item => (
              <option key={item.client_id} value={item.client_id}>
                {item.client_name || 'OAuth Client'} — {item.client_id.slice(0, 12)}…{item.active ? ' (đang chọn)' : ''}
              </option>
            ))}
            <option value="__new__">＋ Thêm OAuth Client mới</option>
          </select>
        </label>
        <input
          className="version-select"
          style={{ width: '100%' }}
          value={config.client_name || ''}
          onChange={event => setConfig({ ...config, client_name: event.target.value })}
          placeholder="Tên dễ nhớ, ví dụ: OAuth Kênh Chính hoặc OAuth Review"
        />
        <input
          className="version-select"
          style={{ width: '100%' }}
          value={config.client_id}
          onChange={event => setConfig({
            ...config,
            client_id: event.target.value,
            client_secret_configured: oauthConfigs.some(item => item.client_id === event.target.value)
          })}
          placeholder="Google OAuth Client ID"
        />
        <input
          className="version-select"
          style={{ width: '100%' }}
          type="password"
          value={config.client_secret}
          onChange={event => setConfig({ ...config, client_secret: event.target.value })}
          placeholder={config.client_secret_configured ? 'Client Secret đã lưu — nhập để thay đổi' : 'Google OAuth Client Secret'}
        />
        <input
          className="version-select"
          style={{ width: '100%' }}
          value={config.redirect_uri}
          onChange={event => setConfig({ ...config, redirect_uri: event.target.value })}
          placeholder="Redirect URI"
        />
        {gpmProfiles.length > 0 && (
          <div style={{ marginTop: 4 }}>
            <label style={{ display: 'block', fontSize: '12px', color: '#93c5fd', marginBottom: 4 }}>
              🛡️ GPM Profile gán cho kênh mới (Mở xác thực & cô lập IP qua Profile này):
            </label>
            <select
              className="version-select"
              style={{ width: '100%', borderColor: '#3b82f6' }}
              value={newChannelGpmProfileId}
              onChange={e => setNewChannelGpmProfileId(e.target.value)}
            >
              <option value="">-- Chọn GPM Profile (Khuyên dùng để cô lập IP) --</option>
              {filteredGpmProfiles.map(p => (
                <option key={p.id} value={p.id}>
                  {p.name} {p.proxy_display ? `[Proxy: ${p.proxy_display}]` : '[No Proxy]'}
                </option>
              ))}
            </select>
          </div>
        )}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <button className="btn-save" disabled={busy} onClick={saveConfig}>💾 Lưu OAuth</button>
          <button className="btn-run" disabled={busy || !config.client_id || !config.client_secret_configured} onClick={() => connectChannel({ gpmProfileId: newChannelGpmProfileId })}>
            ➕ Kết nối kênh YouTube mới
          </button>
          <button className="btn-secondary" disabled={busy} onClick={load}>↻ Làm mới</button>
        </div>
      </div>

      {/* Prompt Selection */}
      <div className="result-panel" style={{ marginTop: 14, padding: 16 }}>
        <label style={{ display: 'block', marginBottom: 8 }}>
          Chọn bộ prompt để cài đặt kênh
        </label>
        <select
          className="version-select"
          style={{ width: '100%' }}
          value={selectedPromptVersion}
          onChange={event => {
            setSelectedPromptVersion(event.target.value)
            setSelectedChannelIdOverride('')
          }}
          disabled={!promptEntries.length}
        >
          {!promptEntries.length && <option value="">Chưa có bộ prompt</option>}
          {promptEntries.map(([versionId, version]) => (
            <option key={versionId} value={versionId}>
              {version.name || versionId}
            </option>
          ))}
        </select>
        {selectedPrompt && !promptDefaultChannelId && (
          <div className="help-text" style={{ marginTop: 8, color: '#f5b041' }}>
            Bộ prompt này chưa được chọn kênh YouTube mặc định.
          </div>
        )}
        {promptDefaultChannelId && !selectedChannel && (
          <div className="help-text" style={{ marginTop: 8, color: '#ff6b6b' }}>
            Kênh của bộ prompt này đã ngắt kết nối. Hãy chọn lại kênh mặc định ở phần cài đặt bộ prompt.
          </div>
        )}
      </div>

      {/* Channel Switcher Tabs if multiple channels exist */}
      {channels.length > 1 && (
        <div className="result-panel" style={{ marginTop: 14, padding: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
            <div>
              <strong>📺 Chuyển nhanh giữa các kênh ({channels.length} kênh):</strong>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            {channels.map(ch => {
              const isSelected = selectedChannel?.id === ch.id
              return (
                <button
                  key={ch.id}
                  type="button"
                  className={isSelected ? 'btn-save' : 'btn-secondary'}
                  onClick={() => setSelectedChannelIdOverride(ch.channel_id)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '6px 12px',
                    borderRadius: 6,
                    border: isSelected ? '1px solid #3b82f6' : '1px solid #475569'
                  }}
                >
                  {ch.thumbnail_url && <img src={ch.thumbnail_url} alt="" style={{ width: 20, height: 20, borderRadius: '50%' }} />}
                  <span style={{ fontWeight: isSelected ? 'bold' : 'normal' }}>{ch.title}</span>
                  {ch.gpm_profile_name ? (
                    <span style={{ fontSize: '10px', background: 'rgba(52, 211, 153, 0.25)', color: '#34d399', padding: '1px 5px', borderRadius: 4 }}>
                      🛡️ {ch.gpm_profile_name}
                    </span>
                  ) : (
                    <span style={{ fontSize: '10px', background: 'rgba(239, 68, 68, 0.25)', color: '#fca5a5', padding: '1px 5px', borderRadius: 4 }}>
                      ⚠️ Chưa gán GPM
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Selected Channel Details */}
      {selectedChannel && [selectedChannel].map(channel => (
        <div key={channel.id} className="result-panel" style={{ marginTop: 14, padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {channel.thumbnail_url && <img src={channel.thumbnail_url} alt="" style={{ width: 42, height: 42, borderRadius: '50%' }} />}
            <div style={{ flex: 1 }}>
              <strong style={{ fontSize: '1.05rem' }}>{channel.title}</strong>
              <div className="help-text">{channel.channel_id}</div>
              <div className="help-text">
                OAuth Client: {channel.oauth_client_id || 'Chưa xác định (sẽ tự nhận diện khi làm mới token)'}
              </div>
            </div>
            <button className="btn-danger" disabled={busy} onClick={() => disconnect(channel)}>Ngắt kết nối</button>
          </div>

          {/* GPM Profile Mapping Box for this Channel */}
          <div style={{ marginTop: 16, padding: 16, background: '#0f172a', borderRadius: 8, border: '1px solid #2563eb' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
              <label style={{ fontWeight: 'bold', color: '#60a5fa', fontSize: '0.95rem' }}>
                🛡️ Gán Profile GPM-Login (Cô lập IP Kênh) — ({gpmProfiles.length} profiles có sẵn)
              </label>
              {(channel.gpm_proxy_display || channel.gpm_proxy_info) && (
                <span style={{ fontSize: '11px', color: '#34d399', background: 'rgba(6, 95, 70, 0.4)', padding: '2px 8px', borderRadius: 4 }}>
                  Proxy: {channel.gpm_proxy_display || channel.gpm_proxy_info}
                </span>
              )}
            </div>

            {/* Quick search filter if many profiles */}
            {gpmProfiles.length > 10 && (
              <div style={{ marginTop: 8 }}>
                <input
                  className="version-select"
                  style={{ width: '100%', fontSize: '12px' }}
                  placeholder="🔍 Gõ để lọc nhanh tên profile hoặc proxy..."
                  value={gpmSearchFilter}
                  onChange={e => setGpmSearchFilter(e.target.value)}
                />
              </div>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) auto auto auto auto', gap: 6, marginTop: 10 }}>
              <select
                className="version-select"
                style={{ width: '100%', fontWeight: '500' }}
                value={channel.gpm_profile_id || ''}
                onChange={e => handleSelectGpmProfile(channel.id, e.target.value)}
              >
                <option value="">-- Chọn Profile GPM tương ứng --</option>
                {filteredGpmProfiles.map(p => (
                  <option key={p.id} value={p.id}>
                    {p.name} {p.proxy_display ? `[Proxy: ${p.proxy_display}]` : '[No Proxy]'}
                  </option>
                ))}
              </select>
              <button
                className="btn-run"
                type="button"
                disabled={gpmBusy || !channel.gpm_profile_id}
                onClick={() => startGpmProfileHandler(channel.gpm_profile_id)}
                title="Mở profile trên GPM để kiểm tra hoặc đăng nhập Google thủ công"
              >
                🚀 Mở Profile
              </button>
              <button
                className="btn-secondary"
                type="button"
                disabled={gpmBusy || !channel.gpm_profile_id}
                onClick={() => openStudioHandler(channel.id)}
                title="Mở thẳng YouTube Studio bên trong Profile GPM của kênh này"
              >
                📺 Vào Studio
              </button>
              <button
                className="btn-secondary"
                type="button"
                disabled={gpmBusy || !channel.gpm_profile_id}
                onClick={() => verifyStudioHandler(channel.id)}
                title="Kiểm tra xem Profile GPM đã đăng nhập YouTube Studio chưa"
              >
                🔍 Kiểm tra
              </button>
              <button
                className="btn-secondary"
                type="button"
                disabled={gpmBusy || !channel.gpm_profile_id}
                onClick={() => stopGpmProfileHandler(channel.gpm_profile_id)}
                title="Đóng trình duyệt profile GPM này"
              >
                ⏹ Đóng Profile
              </button>
            </div>

            <div className="help-text" style={{ marginTop: 8 }}>
              {channel.gpm_profile_id ? (
                <span style={{ color: '#34d399' }}>
                  ✅ Kênh <strong>{channel.title}</strong> đã được gán vào Profile: <strong>{channel.gpm_profile_name || channel.gpm_profile_id}</strong>. Mọi tác vụ upload/bình luận của kênh này sẽ đi qua IP của profile đó. (Nhớ bấm nút <strong>"💾 Lưu kênh"</strong> bên dưới).
                </span>
              ) : (
                <span style={{ color: '#fca5a5' }}>
                  ⚠️ Kênh này chưa được gán Profile GPM. Hãy chọn 1 profile trong danh sách trên để cô lập IP.
                </span>
              )}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) auto', gap: 10, marginTop: 14 }}>
            <select
              className="version-select"
              value={channel.oauth_client_choice || ''}
              onChange={event => changeChannel(channel.id, 'oauth_client_choice', event.target.value)}
            >
              <option value="">Chọn OAuth Client cho kênh này</option>
              {oauthConfigs.map(item => (
                <option key={item.client_id} value={item.client_id}>
                  {item.client_name || 'OAuth Client'} — {item.client_id.slice(0, 12)}…
                </option>
              ))}
            </select>
            <button
              className="btn-run"
              disabled={busy || (!channel.oauth_client_choice && !config.client_id && oauthConfigs.length === 0)}
              onClick={() => connectChannel({
                clientId: channel.oauth_client_choice || config.client_id || (oauthConfigs[0]?.client_id || ''),
                expectedChannel: channel
              })}
            >
              {busy ? '⏳ Đang kết nối...' : '↻ Kết nối lại đúng kênh này'}
            </button>
          </div>
          <div className="help-text" style={{ marginTop: 6 }}>
            Nếu Google hiển thị lỗi 403 khi ứng dụng đang ở chế độ Testing, hãy thêm tài khoản Google quản lý kênh vào danh sách Test users của đúng OAuth Client.
          </div>

          {/* Comment automation settings */}
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1fr) 170px', gap: 10, marginTop: 12 }}>
            <select
              className="version-select"
              value={channel.auto_mode || 'draft_only'}
              onChange={event => changeChannel(channel.id, 'auto_mode', event.target.value)}
            >
              <option value="manual">Chỉ đồng bộ</option>
              <option value="draft_only">Tự soạn bản nháp</option>
              <option value="auto_publish">Tự soạn và đăng</option>
            </select>
            <input
              className="version-select"
              type="number"
              min="1"
              max="1000"
              value={channel.daily_reply_limit ?? 50}
              onChange={event => changeChannel(channel.id, 'daily_reply_limit', event.target.value)}
              title="Giới hạn trả lời mỗi ngày"
            />
          </div>
          <div className="help-text" style={{ marginTop: 6 }}>
            Ô bên phải là số câu trả lời tối đa mỗi ngày. Mặc định 50.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10, marginTop: 10 }}>
            <label style={{ color: '#aaa' }}>
              Cách nhau tối thiểu (phút)
              <input
                className="version-select"
                type="number"
                min="1"
                max="120"
                value={channel.reply_interval_minutes ?? 5}
                onChange={event => changeChannel(channel.id, 'reply_interval_minutes', event.target.value)}
              />
            </label>
            <label style={{ color: '#aaa' }}>
              Tối đa / 15 phút
              <input
                className="version-select"
                type="number"
                min="1"
                max="30"
                value={channel.quarter_hour_reply_limit ?? 3}
                onChange={event => changeChannel(channel.id, 'quarter_hour_reply_limit', event.target.value)}
              />
            </label>
            <label style={{ color: '#aaa' }}>
              Tối đa / giờ
              <input
                className="version-select"
                type="number"
                min="1"
                max="200"
                value={channel.hourly_reply_limit ?? 10}
                onChange={event => changeChannel(channel.id, 'hourly_reply_limit', event.target.value)}
              />
            </label>
            <label style={{ color: '#aaa' }}>
              Cùng video / 30 phút
              <input
                className="version-select"
                type="number"
                min="1"
                max="30"
                value={channel.video_half_hour_reply_limit ?? 3}
                onChange={event => changeChannel(channel.id, 'video_half_hour_reply_limit', event.target.value)}
              />
            </label>
            <label style={{ color: '#aaa' }}>
              Bình luận cũ / ngày
              <input
                className="version-select"
                type="number"
                min="1"
                max="200"
                value={channel.backlog_daily_reply_limit ?? 20}
                onChange={event => changeChannel(channel.id, 'backlog_daily_reply_limit', event.target.value)}
              />
            </label>
            <label style={{ color: '#aaa' }}>
              Bắt đầu đăng
              <input
                className="version-select"
                type="time"
                value={channel.reply_window_start || '08:00'}
                onChange={event => changeChannel(channel.id, 'reply_window_start', event.target.value)}
              />
            </label>
            <label style={{ color: '#aaa' }}>
              Kết thúc đăng
              <input
                className="version-select"
                type="time"
                value={channel.reply_window_end || '22:00'}
                onChange={event => changeChannel(channel.id, 'reply_window_end', event.target.value)}
              />
            </label>
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
            <label style={{ color: channel.reply_paused ? '#f5b041' : '#ddd' }}>
              <input
                type="checkbox"
                checked={Boolean(channel.reply_paused)}
                onChange={event => changeChannel(channel.id, 'reply_paused', event.target.checked)}
              /> Tạm dừng đăng tự động
            </label>
            <label style={{ color: '#ddd' }}>
              <input
                type="checkbox"
                checked={Boolean(channel.auto_sync)}
                onChange={event => changeChannel(channel.id, 'auto_sync', event.target.checked)}
              /> Tự đồng bộ
            </label>
            <label style={{ color: '#ddd' }} title="Tự động bấm thả tim của tác giả (Creator Heart) khi gửi câu trả lời qua GPM Browser">
              <input
                type="checkbox"
                checked={channel.auto_heart !== undefined ? Boolean(channel.auto_heart) : true}
                onChange={event => changeChannel(channel.id, 'auto_heart', event.target.checked)}
              /> ❤️ Thả tim bình luận khi trả lời (qua GPM)
            </label>
            <label style={{ color: '#aaa' }}>
              Chu kỳ (phút):{' '}
              <input
                type="number"
                min="2"
                max="1440"
                value={channel.sync_interval_minutes || 10}
                onChange={event => changeChannel(channel.id, 'sync_interval_minutes', event.target.value)}
                style={{ width: 90 }}
              />
            </label>
          </div>
          <div className="help-text" style={{ marginTop: 8 }}>
            Hệ thống chỉ đăng một câu tại một thời điểm, kiểm tra lại YouTube ngay trước khi đăng và tự hẹn lại khi chạm giới hạn.
          </div>

          {/* Video Publication Schedule */}
          <div style={{ marginTop: 18, paddingTop: 16, borderTop: '1px solid #3a3a3a' }}>
            <strong style={{ color: '#c39bd3' }}>Lịch tự động đăng video</strong>
            <div className="help-text" style={{ marginTop: 6 }}>
              Chỉ dùng khi bộ prompt bật “Đặt lịch đăng”. Slot được giữ cục bộ và đối chiếu lại với YouTube.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10, marginTop: 12 }}>
              <label style={{ color: '#aaa' }}>
                Timezone IANA
                <input
                  className="version-select"
                  value={channel.publication_timezone || 'Asia/Ho_Chi_Minh'}
                  onChange={event => changeChannel(channel.id, 'publication_timezone', event.target.value)}
                  placeholder="Asia/Ho_Chi_Minh"
                />
              </label>
              <label style={{ color: '#aaa' }}>
                Tối đa video / ngày
                <input
                  className="version-select"
                  type="number"
                  min="1"
                  max="20"
                  value={channel.publication_daily_limit ?? 1}
                  onChange={event => changeChannel(channel.id, 'publication_daily_limit', event.target.value)}
                />
              </label>
              <label style={{ color: '#aaa' }}>
                Khoảng an toàn (phút)
                <input
                  className="version-select"
                  type="number"
                  min="1"
                  max="10080"
                  value={channel.publication_lead_minutes ?? 120}
                  onChange={event => changeChannel(channel.id, 'publication_lead_minutes', event.target.value)}
                />
              </label>
            </div>
            <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
              {(Array.isArray(channel.publication_slots) ? channel.publication_slots : []).map((slot, index) => (
                <div key={`${index}-${slot.day}-${slot.time}`} style={{ display: 'grid', gridTemplateColumns: '1fr 140px auto', gap: 8 }}>
                  <select
                    className="version-select"
                    value={slot.day}
                    onChange={event => changePublicationSlot(channel, index, 'day', event.target.value)}
                  >
                    {WEEKDAYS.map((label, day) => <option key={label} value={day}>{label}</option>)}
                  </select>
                  <input
                    className="version-select"
                    type="time"
                    value={slot.time || '09:00'}
                    onChange={event => changePublicationSlot(channel, index, 'time', event.target.value)}
                  />
                  <button className="btn-danger" type="button" onClick={() => removePublicationSlot(channel, index)}>Xóa</button>
                </div>
              ))}
              <button className="btn-secondary" type="button" onClick={() => addPublicationSlot(channel)}>
                + Thêm khung giờ đăng
              </button>
            </div>
            <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', marginTop: 12 }}>
              <label style={{ color: channel.publication_paused ? '#f5b041' : '#ddd' }}>
                <input
                  type="checkbox"
                  checked={Boolean(channel.publication_paused)}
                  onChange={event => changeChannel(channel.id, 'publication_paused', event.target.checked)}
                /> Tạm dừng toàn bộ tự động đăng video
              </label>
              <label style={{ color: '#ddd' }}>
                <input
                  type="checkbox"
                  checked={Boolean(channel.public_upload_verified)}
                  onChange={event => changeChannel(channel.id, 'public_upload_verified', event.target.checked)}
                /> Đã xác minh API project được phép đặt lịch công khai
              </label>
            </div>
          </div>
          <textarea
            className="prompt-textarea"
            style={{ minHeight: 90, marginTop: 10 }}
            value={channel.reply_instruction || ''}
            onChange={event => changeChannel(channel.id, 'reply_instruction', event.target.value)}
            placeholder="Phong cách trả lời riêng của kênh (không bắt buộc)"
          />
          <button className="btn-save" disabled={busy} onClick={() => saveChannel(channel)}>💾 Lưu kênh</button>
        </div>
      ))}
      {message && <div style={{ color: message.startsWith('❌') ? '#ff6b6b' : '#4dd0e1', marginTop: 12 }}>{message}</div>}
    </div>
  )
}
