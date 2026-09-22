const API_BASE = 'http://127.0.0.1:8080'

/**
 * Open a custom URL inside a specific GPM Profile browser session.
 */
export async function openUrlInGpm(profileId, url, profileName = '') {
  const cleanId = String(profileId || '').trim()
  if (!cleanId) {
    throw new Error('Chưa chọn GPM Profile để mở cửa sổ.')
  }
  const res = await fetch(`${API_BASE}/api/gpm/profiles/${encodeURIComponent(cleanId)}/open-url`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url })
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || `Lỗi HTTP ${res.status}`)
  }
  return data
}

/**
 * Open YouTube Studio video editor inside the channel's GPM profile for a video.
 */
export async function openVideoStudioInGpm(videoId) {
  const res = await fetch(`${API_BASE}/api/videos/${videoId}/open-studio`, {
    method: 'POST'
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || `Lỗi HTTP ${res.status}`)
  }
  return data
}

/**
 * Open YouTube Watch page inside the channel's GPM profile for a video.
 */
export async function openVideoWatchInGpm(videoId) {
  const res = await fetch(`${API_BASE}/api/videos/${videoId}/open-watch`, {
    method: 'POST'
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || `Lỗi HTTP ${res.status}`)
  }
  return data
}

/**
 * Open YouTube Studio dashboard inside the channel's GPM profile.
 */
export async function openChannelStudioInGpm(channelDbId) {
  const res = await fetch(`${API_BASE}/api/youtube-comments/channels/${channelDbId}/open-studio`, {
    method: 'POST'
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || `Lỗi HTTP ${res.status}`)
  }
  return data
}
