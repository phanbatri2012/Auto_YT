import { useCallback, useEffect, useState } from 'react'

const API_BASE = 'http://127.0.0.1:8080'

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
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const promptEntries = Object.entries(promptVersions)
  const selectedPrompt = promptVersions[selectedPromptVersion] || null
  const selectedChannelId = String(
    selectedPrompt?.default_youtube_channel_id || ''
  ).trim()
  const selectedChannel = channels.find(
    channel => String(channel.channel_id || '') === selectedChannelId
  ) || null

  useEffect(() => {
    setSelectedPromptVersion(previous => {
      if (activePromptVersion && promptVersions[activePromptVersion]) {
        return activePromptVersion
      }
      return promptVersions[previous] ? previous : (Object.keys(promptVersions)[0] || '')
    })
  }, [activePromptVersion, promptVersions])

  const load = useCallback(async () => {
    try {
      const [configResponse, channelsResponse] = await Promise.all([
        fetch(`${API_BASE}/api/youtube-comments/oauth/config`),
        fetch(`${API_BASE}/api/youtube-comments/channels`)
      ])
      const configData = await configResponse.json()
      const channelData = await channelsResponse.json()
      setConfig(previous => ({
        ...previous,
        ...configData,
        client_secret: ''
      }))
      setOauthConfigs(Array.isArray(configData.items) ? configData.items : [])
      const items = Array.isArray(channelData.items) ? channelData.items : []
      setChannels(items.map(channel => ({
        ...channel,
        oauth_client_choice: channel.oauth_client_id || ''
      })))
      onChannelsChange?.(items)
    } catch (error) {
      setMessage(`❌ Không thể đọc cấu hình YouTube: ${error.message}`)
    }
  }, [onChannelsChange])

  useEffect(() => {
    load()
    const onFocus = () => load()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [load])

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

  const connectChannel = async ({ clientId = '', expectedChannel = null } = {}) => {
    setBusy(true)
    try {
      const selectedClientId = String(clientId || config.client_id || '').trim()
      const query = new URLSearchParams({ client_id: selectedClientId })
      if (expectedChannel?.channel_id) {
        query.set('expected_channel_id', expectedChannel.channel_id)
      }
      const response = await fetch(`${API_BASE}/api/youtube-comments/oauth/start?${query.toString()}`, {
        method: 'POST'
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      window.open(data.authorization_url, 'youtube-oauth', 'width=720,height=820')
      setMessage(
        expectedChannel
          ? `Hãy chọn tài khoản Google quản lý đúng kênh ${expectedChannel.title}. Tên hiện trên màn hình Google là tên ứng dụng OAuth, không phải tên kênh.`
          : 'Hãy cấp quyền trong cửa sổ Google vừa mở. Danh sách sẽ tự cập nhật khi bạn quay lại.'
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
          sync_interval_minutes: Number(channel.sync_interval_minutes) || 10
        })
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      changeChannel(channel.id, 'updated_at', data.updated_at)
      setMessage(`✅ Đã lưu thiết lập kênh ${channel.title}.`)
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

  return (
    <div className="prompt-item" style={{ marginBottom: 20 }}>
      <div className="prompt-header">
        <div>
          <label>📺 Kênh YouTube & trả lời bình luận</label>
          <div className="help-text" style={{ marginTop: 5 }}>
            Mỗi kênh giữ token và liên kết OAuth Client riêng; nhiều kênh vẫn có thể dùng chung một Client nếu mọi tài khoản đều được Google cho phép. Client Secret và token không được trả về giao diện hoặc đưa lên Git.
          </div>
        </div>
      </div>

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
          placeholder="Tên dễ nhớ, ví dụ: OAuth GKVS hoặc OAuth THHN"
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
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <button className="btn-save" disabled={busy} onClick={saveConfig}>💾 Lưu OAuth</button>
          <button className="btn-run" disabled={busy || !config.client_id || !config.client_secret_configured} onClick={() => connectChannel()}>
            ➕ Kết nối kênh YouTube mới
          </button>
          <button className="btn-secondary" disabled={busy} onClick={load}>↻ Làm mới</button>
        </div>
      </div>

      <div className="result-panel" style={{ marginTop: 14, padding: 16 }}>
        <label style={{ display: 'block', marginBottom: 8 }}>
          Chọn bộ prompt để cài đặt kênh
        </label>
        <select
          className="version-select"
          style={{ width: '100%' }}
          value={selectedPromptVersion}
          onChange={event => setSelectedPromptVersion(event.target.value)}
          disabled={!promptEntries.length}
        >
          {!promptEntries.length && <option value="">Chưa có bộ prompt</option>}
          {promptEntries.map(([versionId, version]) => (
            <option key={versionId} value={versionId}>
              {version.name || versionId}
            </option>
          ))}
        </select>
        {selectedPrompt && !selectedChannelId && (
          <div className="help-text" style={{ marginTop: 8, color: '#f5b041' }}>
            Bộ prompt này chưa được chọn kênh YouTube mặc định.
          </div>
        )}
        {selectedChannelId && !selectedChannel && (
          <div className="help-text" style={{ marginTop: 8, color: '#ff6b6b' }}>
            Kênh của bộ prompt này đã ngắt kết nối. Hãy chọn lại kênh mặc định ở phần cài đặt bộ prompt.
          </div>
        )}
      </div>

      {selectedChannel && [selectedChannel].map(channel => (
        <div key={channel.id} className="result-panel" style={{ marginTop: 14, padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {channel.thumbnail_url && <img src={channel.thumbnail_url} alt="" style={{ width: 36, height: 36, borderRadius: '50%' }} />}
            <div style={{ flex: 1 }}>
              <strong>{channel.title}</strong>
              <div className="help-text">{channel.channel_id}</div>
              <div className="help-text">
                OAuth Client: {channel.oauth_client_id || 'Chưa xác định (sẽ tự nhận diện khi làm mới token)'}
              </div>
            </div>
            <button className="btn-danger" disabled={busy} onClick={() => disconnect(channel)}>Ngắt kết nối</button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) auto', gap: 10, marginTop: 12 }}>
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
              disabled={busy || !channel.oauth_client_choice}
              onClick={() => connectChannel({
                clientId: channel.oauth_client_choice,
                expectedChannel: channel
              })}
            >
              ↻ Kết nối lại đúng kênh này
            </button>
          </div>
          <div className="help-text" style={{ marginTop: 6 }}>
            Nếu Google hiển thị lỗi 403 khi ứng dụng đang ở chế độ Testing, hãy thêm tài khoản Google quản lý kênh vào danh sách Test users của đúng OAuth Client.
          </div>
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
