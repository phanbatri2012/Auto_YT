import { useCallback, useEffect, useState, useMemo } from 'react'
import './ChannelManager.css'
import { useSubRoute } from './router.js'
import './Settings.css'

const API_BASE = 'http://127.0.0.1:8080'
const WEEKDAYS = ['Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy', 'Chủ Nhật']
const FB_STORAGE_KEY = 'AUTOYT_FACEBOOK_PAGES'
const TT_STORAGE_KEY = 'AUTOYT_TIKTOK_ACCOUNTS'
const VALID_TABS = ['youtube', 'facebook', 'tiktok', 'gpm']

function withoutFacebookSecrets(page) {
  const { access_token: legacyToken, ...safePage } = page || {}
  return {
    ...safePage,
    token_configured: Boolean(safePage.token_configured || legacyToken)
  }
}

function readFacebookPageStorage() {
  try {
    const saved = localStorage.getItem(FB_STORAGE_KEY)
    const parsed = saved ? JSON.parse(saved) : []
    const pages = Array.isArray(parsed) ? parsed : []
    return {
      safePages: pages.map(withoutFacebookSecrets),
      legacyCredentials: pages.filter(page => page?.access_token && page?.page_id)
    }
  } catch {
    return { safePages: [], legacyCredentials: [] }
  }
}

export default function ChannelManager({
  onChannelsChange,
  promptVersions = {},
  activePromptVersion = ''
}) {
  const [subRoute, setSubRoute] = useSubRoute('channels', 'youtube')
  const initialTab = VALID_TABS.includes(subRoute) ? subRoute : 'youtube'
  const [activeTab, setActiveTabState] = useState(initialTab)

  useEffect(() => {
    if (subRoute && VALID_TABS.includes(subRoute) && subRoute !== activeTab) {
      setActiveTabState(subRoute)
    }
  }, [subRoute, activeTab])

  const setActiveTab = (tab) => {
    setActiveTabState(tab)
    setSubRoute(tab)
  }

  // YouTube States
  const [config, setConfig] = useState({
    client_id: '',
    client_name: '',
    client_secret: '',
    redirect_uri: `${API_BASE}/api/youtube-comments/oauth/callback`,
    client_secret_configured: false
  })
  const [oauthConfigs, setOauthConfigs] = useState([])
  const [channels, setChannels] = useState([])
  const [selectedPromptVersion, setSelectedPromptVersion] = useState(activePromptVersion)
  const [selectedChannelIdOverride, setSelectedChannelIdOverride] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  // GPM-Login v3 States
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

  // Local Browser Profiles (Cốc Cốc, Chrome, Edge)
  const [localProfiles, setLocalProfiles] = useState([])
  const [scanningPlatform, setScanningPlatform] = useState(null) // 'youtube' | 'facebook' | 'tiktok' | null
  const [scannerProfileId, setScannerProfileId] = useState('')
  const [discoveredFbPages, setDiscoveredFbPages] = useState([])
  const [discoveredTiktok, setDiscoveredTiktok] = useState(null)

  // Accordions for manual / advanced settings
  const [showAdvancedOAuth, setShowAdvancedOAuth] = useState(false)
  const [showManualFb, setShowManualFb] = useState(false)
  const [showManualTiktok, setShowManualTiktok] = useState(false)

  // Facebook Pages States
  const [initialFacebookStorage] = useState(readFacebookPageStorage)
  const [facebookPages, setFacebookPages] = useState(initialFacebookStorage.safePages)
  const [legacyFacebookCredentials, setLegacyFacebookCredentials] = useState(
    initialFacebookStorage.legacyCredentials
  )
  const [newFbPage, setNewFbPage] = useState({
    name: '',
    page_id: '',
    gpm_profile_id: '',
    access_token: '',
    auto_reels: true,
    auto_comment: true
  })
  const [isAddingFbPage, setIsAddingFbPage] = useState(false)

  // TikTok Accounts States
  const [tiktokAccounts, setTiktokAccounts] = useState(() => {
    try {
      const saved = localStorage.getItem(TT_STORAGE_KEY)
      return saved ? JSON.parse(saved) : []
    } catch {
      return []
    }
  })
  const [newTiktok, setNewTiktok] = useState({
    name: '',
    handle: '',
    gpm_profile_id: '',
    session_id: '',
    auto_video: true,
    auto_comment: true
  })
  const [isAddingTiktok, setIsAddingTiktok] = useState(false)

  useEffect(() => {
    if (legacyFacebookCredentials.length === 0) return
    let cancelled = false

    const migrateLegacyCredentials = async () => {
      try {
        await Promise.all(legacyFacebookCredentials.map(async page => {
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
          const data = await response.json().catch(() => ({}))
          if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
        }))
        if (!cancelled) {
          setLegacyFacebookCredentials([])
          setMessage('✅ Đã chuyển Page Access Token cũ sang kho mã hóa an toàn.')
        }
      } catch (error) {
        if (!cancelled) {
          setMessage(`❌ Chưa thể chuyển Page Access Token cũ: ${error.message}`)
        }
      }
    }

    migrateLegacyCredentials()
    return () => { cancelled = true }
  }, [legacyFacebookCredentials])

  // Save only non-secret Facebook page metadata to localStorage.
  useEffect(() => {
    if (legacyFacebookCredentials.length > 0) return
    try {
      localStorage.setItem(
        FB_STORAGE_KEY,
        JSON.stringify(facebookPages.map(withoutFacebookSecrets))
      )
    } catch (e) {
      console.warn('Không thể lưu Facebook pages vào localStorage:', e)
    }
  }, [facebookPages, legacyFacebookCredentials])

  // Save TikTok accounts to localStorage
  useEffect(() => {
    try {
      localStorage.setItem(TT_STORAGE_KEY, JSON.stringify(tiktokAccounts))
    } catch (e) {
      console.warn('Không thể lưu TikTok accounts vào localStorage:', e)
    }
  }, [tiktokAccounts])

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

  const loadGpm = useCallback(async () => {
    try {
      const [configRes, statusRes] = await Promise.all([
        fetch(`${API_BASE}/api/gpm/config`),
        fetch(`${API_BASE}/api/gpm/status`)
      ])
      const configData = await configRes.json()
      const statusData = await statusRes.json()
      setGpmConfig(configData)
      setGpmStatus(statusData)

      if (statusData.online) {
        await loadGpmProfilesList(configData.api_url)
      }
    } catch (error) {
      console.warn('Lỗi load GPM:', error)
    }
  }, [loadGpmProfilesList])

  const loadLocalProfiles = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/channels/local-profiles`)
      if (res.ok) {
        const data = await res.json()
        setLocalProfiles(Array.isArray(data.items) ? data.items : [])
      }
    } catch (error) {
      console.warn('Lỗi load local browser profiles:', error)
    }
  }, [])

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
      const defaultClientId = configData.client_id || (Array.isArray(configData.items) && configData.items[0]?.client_id) || ''
      setChannels(items.map(channel => ({
        ...channel,
        oauth_client_choice: channel.oauth_client_id || defaultClientId
      })))
      onChannelsChange?.(items)
      await Promise.all([loadGpm(), loadLocalProfiles()])
    } catch (error) {
      setMessage(`❌ Không thể đọc cấu hình YouTube: ${error.message}`)
    }
  }, [onChannelsChange, loadGpm, loadLocalProfiles])

  useEffect(() => {
    load()
    const onFocus = () => load()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [load])

  // Scanner Handlers (1-Click Browser Open & CDP Scan)
  const openPlatformBrowserHandler = async (profileId, platform) => {
    if (!profileId) {
      setMessage('⚠️ Vui lòng chọn Profile trình duyệt trước khi mở.')
      return
    }
    setGpmBusy(true)
    setMessage(`⏳ Đang mở trình duyệt cho ${platform.toUpperCase()}...`)
    try {
      const res = await fetch(`${API_BASE}/api/channels/open-browser`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: profileId, platform })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(`🚀 ${data.message || 'Đã mở trình duyệt thành công. Bạn hãy đăng nhập nếu cần rồi bấm Quét kênh.'}`)
    } catch (error) {
      setMessage(`❌ Lỗi khi mở trình duyệt: ${error.message}`)
    } finally {
      setGpmBusy(false)
    }
  }

  const scanYouTubeHandler = async (profileId) => {
    if (!profileId) {
      setMessage('⚠️ Vui lòng chọn Profile trước khi quét kênh.')
      return
    }
    setScanningPlatform('youtube')
    setMessage('⏳ Đang kết nối CDP và quét thông tin kênh YouTube Studio...')
    try {
      const res = await fetch(`${API_BASE}/api/channels/scan/youtube`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: profileId, auto_save: true })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      if (data.logged_in && data.channel) {
        setMessage(`✅ ${data.message}`)
        await load()
        setSelectedChannelIdOverride(data.channel.channel_id)
      } else {
        setMessage(`⚠️ ${data.message || 'Không thể nhận diện kênh YouTube. Hãy mở trình duyệt và đăng nhập trước.'}`)
      }
    } catch (error) {
      setMessage(`❌ Lỗi quét YouTube: ${error.message}`)
    } finally {
      setScanningPlatform(null)
    }
  }

  const scanFacebookHandler = async (profileId) => {
    if (!profileId) {
      setMessage('⚠️ Vui lòng chọn Profile trước khi quét Fanpage.')
      return
    }
    setScanningPlatform('facebook')
    setMessage('⏳ Đang kết nối CDP và quét danh sách Fanpage Facebook...')
    try {
      const res = await fetch(`${API_BASE}/api/channels/scan/facebook`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: profileId })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      if (data.logged_in && Array.isArray(data.pages)) {
        setDiscoveredFbPages(data.pages)
        setMessage(data.pages.length > 0
          ? `✅ Tìm thấy ${data.pages.length} Fanpage trong phiên đăng nhập! Hãy chọn Fanpage muốn liên kết bên dưới.`
          : '⚠️ Đã kết nối Facebook nhưng không tìm thấy Fanpage nào bạn đang quản lý.')
      } else {
        setMessage(`⚠️ ${data.message || 'Chưa đăng nhập Facebook trong trình duyệt.'}`)
      }
    } catch (error) {
      setMessage(`❌ Lỗi quét Facebook: ${error.message}`)
    } finally {
      setScanningPlatform(null)
    }
  }

  const linkDiscoveredFbPageHandler = (page) => {
    if (facebookPages.some(p => p.page_id === page.page_id)) {
      setMessage(`⚠️ Fanpage "${page.name}" (${page.page_id}) đã có trong danh sách.`)
      return
    }
    setFacebookPages(prev => [...prev, withoutFacebookSecrets(page)])
    setMessage(`✅ Đã liên kết Fanpage "${page.name}" thành công!`)
    setDiscoveredFbPages(prev => prev.filter(p => p.page_id !== page.page_id))
  }

  const scanTiktokHandler = async (profileId) => {
    if (!profileId) {
      setMessage('⚠️ Vui lòng chọn Profile trước khi quét Kênh TikTok.')
      return
    }
    setScanningPlatform('tiktok')
    setMessage('⏳ Đang kết nối CDP và quét Kênh TikTok...')
    try {
      const res = await fetch(`${API_BASE}/api/channels/scan/tiktok`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: profileId })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      if (data.logged_in && data.account) {
        setDiscoveredTiktok(data.account)
        setMessage(`✅ ${data.message}`)
      } else {
        setMessage(`⚠️ ${data.message || 'Chưa đăng nhập TikTok trong trình duyệt.'}`)
      }
    } catch (error) {
      setMessage(`❌ Lỗi quét TikTok: ${error.message}`)
    } finally {
      setScanningPlatform(null)
    }
  }

  const linkDiscoveredTiktokHandler = (account) => {
    if (tiktokAccounts.some(a => a.handle === account.handle)) {
      setMessage(`⚠️ Kênh TikTok "${account.handle}" đã có trong danh sách.`)
      return
    }
    setTiktokAccounts(prev => [...prev, account])
    setMessage(`✅ Đã liên kết Kênh TikTok "${account.name}" (${account.handle}) thành công!`)
    setDiscoveredTiktok(null)
  }

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

  // Facebook Actions
  const handleAddFacebookPage = async () => {
    if (!newFbPage.name.trim()) {
      setMessage('❌ Vui lòng nhập tên Fanpage.')
      return
    }
    const pageId = newFbPage.page_id.trim()
    const accessToken = newFbPage.access_token.trim()
    if (accessToken && !pageId) {
      setMessage('❌ Phải nhập Fanpage Page ID khi lưu Page Access Token.')
      return
    }
    const selectedGpm = gpmProfiles.find(p => p.id === newFbPage.gpm_profile_id)
    const newPage = {
      id: `fb_${Date.now()}`,
      name: newFbPage.name.trim(),
      page_id: pageId || `PAGE_${Date.now()}`,
      gpm_profile_id: newFbPage.gpm_profile_id,
      gpm_profile_name: selectedGpm ? selectedGpm.name : '',
      gpm_proxy_info: selectedGpm ? (selectedGpm.proxy_display || 'Direct') : '',
      token_configured: Boolean(accessToken),
      auto_reels: newFbPage.auto_reels,
      auto_comment: newFbPage.auto_comment,
      status: 'active',
      created_at: new Date().toISOString()
    }
    if (accessToken) {
      try {
        const response = await fetch(
          `${API_BASE}/api/fb-crossposter/settings?page_id=${encodeURIComponent(pageId)}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              target_fb_page_id: pageId,
              target_fb_page_name: newPage.name,
              target_gpm_profile_id: newPage.gpm_profile_id,
              target_access_token: accessToken
            })
          }
        )
        const data = await response.json().catch(() => ({}))
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
        newPage.token_configured = Boolean(data.settings?.target_access_token_configured)
      } catch (error) {
        setMessage(`❌ Không thể lưu Page Access Token: ${error.message}`)
        return
      }
    }
    setFacebookPages(prev => [...prev, newPage])
    setNewFbPage({
      name: '',
      page_id: '',
      gpm_profile_id: '',
      access_token: '',
      auto_reels: true,
      auto_comment: true
    })
    setIsAddingFbPage(false)
    setMessage(`✅ Đã kết nối Fanpage Facebook "${newPage.name}" thành công.`)
  }

  const handleDeleteFacebookPage = pageId => {
    if (!confirm('Bạn có chắc muốn xóa liên kết Fanpage này?')) return
    setFacebookPages(prev => prev.filter(p => p.id !== pageId))
    setMessage('✅ Đã xóa liên kết Fanpage Facebook.')
  }

  const openFacebookInGpm = async (gpmProfileId) => {
    if (!gpmProfileId) return
    setGpmBusy(true)
    try {
      const res = await fetch(`${API_BASE}/api/gpm/profiles/${encodeURIComponent(gpmProfileId)}/open-url`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: 'https://business.facebook.com' })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage('🚀 Đã mở Facebook Business Suite trong Profile GPM.')
    } catch (error) {
      setMessage(`❌ Lỗi mở Facebook qua GPM: ${error.message}`)
    } finally {
      setGpmBusy(false)
    }
  }

  // TikTok Actions
  const handleAddTiktok = () => {
    if (!newTiktok.name.trim()) {
      setMessage('❌ Vui lòng nhập tên kênh TikTok.')
      return
    }
    const cleanHandle = newTiktok.handle.trim().startsWith('@')
      ? newTiktok.handle.trim()
      : (newTiktok.handle.trim() ? `@${newTiktok.handle.trim()}` : `@user_${Date.now().toString().slice(-6)}`)
    const selectedGpm = gpmProfiles.find(p => p.id === newTiktok.gpm_profile_id)
    const newAccount = {
      id: `tt_${Date.now()}`,
      name: newTiktok.name.trim(),
      handle: cleanHandle,
      gpm_profile_id: newTiktok.gpm_profile_id,
      gpm_profile_name: selectedGpm ? selectedGpm.name : '',
      gpm_proxy_info: selectedGpm ? (selectedGpm.proxy_display || 'Direct') : '',
      session_id: newTiktok.session_id.trim(),
      auto_video: newTiktok.auto_video,
      auto_comment: newTiktok.auto_comment,
      status: 'active',
      created_at: new Date().toISOString()
    }
    setTiktokAccounts(prev => [...prev, newAccount])
    setNewTiktok({
      name: '',
      handle: '',
      gpm_profile_id: '',
      session_id: '',
      auto_video: true,
      auto_comment: true
    })
    setIsAddingTiktok(false)
    setMessage(`✅ Đã kết nối Kênh TikTok "${newAccount.name}" (${newAccount.handle}) thành công.`)
  }

  const handleDeleteTiktok = accountId => {
    if (!confirm('Bạn có chắc muốn xóa liên kết Kênh TikTok này?')) return
    setTiktokAccounts(prev => prev.filter(a => a.id !== accountId))
    setMessage('✅ Đã xóa liên kết Kênh TikTok.')
  }

  const openTiktokInGpm = async (gpmProfileId, handle = '') => {
    if (!gpmProfileId) return
    setGpmBusy(true)
    try {
      const url = 'https://www.tiktok.com/creator-center/upload'
      const res = await fetch(`${API_BASE}/api/gpm/profiles/${encodeURIComponent(gpmProfileId)}/open-url`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(`🚀 Đã mở TikTok Creator Center trong Profile GPM.`)
    } catch (error) {
      setMessage(`❌ Lỗi mở TikTok qua GPM: ${error.message}`)
    } finally {
      setGpmBusy(false)
    }
  }

  // Filtered GPM profiles for search
  const filteredGpmProfiles = gpmProfiles.filter(p => {
    if (!gpmSearchFilter.trim()) return true
    const term = gpmSearchFilter.toLowerCase()
    return (p.name || '').toLowerCase().includes(term) || (p.proxy_display || '').toLowerCase().includes(term) || (p.id || '').toLowerCase().includes(term)
  })

  // Filtered Local browser profiles for search (Cốc Cốc, Chrome, Edge)
  const filteredLocalProfiles = localProfiles.filter(p => {
    if (!gpmSearchFilter.trim()) return true
    const term = gpmSearchFilter.toLowerCase()
    return (p.display_label || '').toLowerCase().includes(term) || (p.browser_name || '').toLowerCase().includes(term) || (p.profile_name || '').toLowerCase().includes(term)
  })

  const renderUnifiedProfileSelect = (value, onChange, currentPlatform = '', placeholder = '-- Chọn Profile (Cốc Cốc / Chrome / GPM) --') => {
    return (
      <select
        className="version-select"
        style={{ width: '100%', fontWeight: '500' }}
        value={value || ''}
        onChange={onChange}
      >
        <option value="">{placeholder}</option>
        {filteredLocalProfiles.length > 0 && (
          <optgroup label="🌐 Trình duyệt Cốc Cốc / Local (Mạng máy thật)">
            {filteredLocalProfiles.map(p => (
              <option key={p.id} value={p.id}>
                {p.display_label}
              </option>
            ))}
          </optgroup>
        )}
        {filteredGpmProfiles.length > 0 && (
          <optgroup label="🛡️ Profiles GPM-Login (Cô lập Proxy riêng)">
            {filteredGpmProfiles.map(p => (
              <option key={p.id} value={p.id}>
                {getProfileDropdownLabel(p, currentPlatform)}
              </option>
            ))}
          </optgroup>
        )}
      </select>
    )
  }

  // Analysis of GPM Profiles for Multi-Platform Matrix
  const getProfileUsage = profileId => {
    const ytAssigned = channels.filter(c => c.gpm_profile_id === profileId)
    const fbAssigned = facebookPages.filter(f => f.gpm_profile_id === profileId)
    const ttAssigned = tiktokAccounts.filter(t => t.gpm_profile_id === profileId)
    const ytCount = ytAssigned.length
    const fbCount = fbAssigned.length
    const ttCount = ttAssigned.length

    let statusType = 'unused'
    let statusText = 'Chưa sử dụng'
    let tooltip = 'Profile rảnh rỗi, có thể gán cho kênh mới'

    const collisions = []
    if (ytCount > 1) collisions.push(`${ytCount} YouTube`)
    if (fbCount > 1) collisions.push(`${fbCount} Facebook`)
    if (ttCount > 1) collisions.push(`${ttCount} TikTok`)

    const activePlatforms = []
    if (ytCount > 0) activePlatforms.push(`YouTube (${ytAssigned.map(c => c.title).join(', ')})`)
    if (fbCount > 0) activePlatforms.push(`Facebook (${fbAssigned.map(f => f.name).join(', ')})`)
    if (ttCount > 0) activePlatforms.push(`TikTok (${ttAssigned.map(t => t.handle).join(', ')})`)

    if (collisions.length > 0) {
      statusType = 'warning-collision'
      statusText = '⚠️ Trùng nền tảng'
      tooltip = `CẢNH BÁO: Trùng lặp cùng nền tảng: ${collisions.join(', ')}. Nguy cơ bị quét liên đới!`
    } else if (activePlatforms.length > 1) {
      statusType = 'shared-safe'
      const platformNames = [ytCount > 0 && 'YT', fbCount > 0 && 'FB', ttCount > 0 && 'TikTok'].filter(Boolean).join(' + ')
      statusText = `✅ Dùng chung ${platformNames}`
      tooltip = `Hợp lệ đa nền tảng: ${activePlatforms.join(' + ')} dùng chung Profile GPM an toàn.`
    } else if (ytCount === 1) {
      statusType = 'isolated'
      statusText = '🟢 Cô lập YouTube (1:1)'
      tooltip = `Profile riêng cho kênh YouTube: ${ytAssigned[0].title}`
    } else if (fbCount === 1) {
      statusType = 'isolated'
      statusText = '🟢 Cô lập Facebook (1:1)'
      tooltip = `Profile riêng cho Fanpage Facebook: ${fbAssigned[0].name}`
    } else if (ttCount === 1) {
      statusType = 'isolated'
      statusText = '🟢 Cô lập TikTok (1:1)'
      tooltip = `Profile riêng cho Kênh TikTok: ${ttAssigned[0].name} (${ttAssigned[0].handle})`
    }

    return { ytAssigned, fbAssigned, ttAssigned, ytCount, fbCount, ttCount, statusType, statusText, tooltip }
  }

  // Format usage text for dropdown options
  const getProfileDropdownLabel = (profile, currentPlatform = '') => {
    const usage = getProfileUsage(profile.id)
    const notes = []
    if (currentPlatform !== 'youtube' && usage.ytCount > 0) {
      notes.push(`YT: ${usage.ytAssigned[0].title}`)
    }
    if (currentPlatform !== 'facebook' && usage.fbCount > 0) {
      notes.push(`FB: ${usage.fbAssigned[0].name}`)
    }
    if (currentPlatform !== 'tiktok' && usage.ttCount > 0) {
      notes.push(`TikTok: ${usage.ttAssigned[0].handle}`)
    }

    let extra = ''
    if (notes.length > 0) {
      extra = ` [✅ Dùng chung: ${notes.join(', ')}]`
    } else if (
      (currentPlatform === 'youtube' && usage.ytCount > 0) ||
      (currentPlatform === 'facebook' && usage.fbCount > 0) ||
      (currentPlatform === 'tiktok' && usage.ttCount > 0)
    ) {
      extra = ` [⚠️ Đang gán kênh cùng nền tảng]`
    }

    return `${profile.name} ${profile.proxy_display ? `[Proxy: ${profile.proxy_display}]` : '[No Proxy]'}${extra}`
  }

  return (
    <div className="channel-hub-container">
      {/* Header */}
      <div className="channel-hub-header">
        <div className="title-group">
          <h1>📡 Channel Hub</h1>
          <p>Trung tâm quản lý kết nối kênh đa nền tảng (YouTube, Facebook, TikTok) & Cô lập Profile GPM-Login an toàn tuyệt đối.</p>
        </div>
      </div>

      {/* Overview Statistics Cards */}
      <div className="hub-stats-row">
        <div className="hub-stat-card">
          <div className="hub-stat-icon" style={{ color: gpmStatus.online ? '#34d399' : '#f87171' }}>
            🛡️
          </div>
          <div className="hub-stat-info">
            <div className="stat-value">{gpmStatus.online ? `${gpmProfiles.length} Profiles` : 'Offline'}</div>
            <div className="stat-label">GPM-Login v3 Local API</div>
          </div>
        </div>

        <div className="hub-stat-card">
          <div className="hub-stat-icon" style={{ color: '#ef4444' }}>
            📺
          </div>
          <div className="hub-stat-info">
            <div className="stat-value">{channels.length} Kênh</div>
            <div className="stat-label">Kênh YouTube đã kết nối</div>
          </div>
        </div>

        <div className="hub-stat-card">
          <div className="hub-stat-icon" style={{ color: '#3b82f6' }}>
            📘
          </div>
          <div className="hub-stat-info">
            <div className="stat-value">{facebookPages.length} Fanpage</div>
            <div className="stat-label">Facebook Pages đã liên kết</div>
          </div>
        </div>

        <div className="hub-stat-card">
          <div className="hub-stat-icon" style={{ color: '#25f4ee' }}>
            🎵
          </div>
          <div className="hub-stat-info">
            <div className="stat-value">{tiktokAccounts.length} Kênh</div>
            <div className="stat-label">Kênh TikTok đã kết nối</div>
          </div>
        </div>

        <div className="hub-stat-card">
          <div className="hub-stat-icon" style={{ color: '#a855f7' }}>
            🔄
          </div>
          <div className="hub-stat-info">
            <div className="stat-value">
              {gpmProfiles.filter(p => {
                const u = getProfileUsage(p.id)
                return u.statusType === 'shared-safe'
              }).length} Profiles
            </div>
            <div className="stat-label">Dùng chung đa nền tảng an toàn</div>
          </div>
        </div>
      </div>

      {/* Navigation Platform Tabs */}
      <div className="platform-tabs">
        <button
          className={`platform-tab-btn ${activeTab === 'youtube' ? 'active' : ''}`}
          onClick={() => setActiveTab('youtube')}
        >
          <span>📺 Kênh YouTube</span>
          <span className="platform-tab-badge">{channels.length}</span>
        </button>
        <button
          className={`platform-tab-btn ${activeTab === 'facebook' ? 'active' : ''}`}
          onClick={() => setActiveTab('facebook')}
        >
          <span>📘 Kênh Facebook</span>
          <span className="platform-tab-badge">{facebookPages.length}</span>
        </button>
        <button
          className={`platform-tab-btn ${activeTab === 'tiktok' ? 'active' : ''}`}
          onClick={() => setActiveTab('tiktok')}
        >
          <span>🎵 Kênh TikTok</span>
          <span className="platform-tab-badge">{tiktokAccounts.length}</span>
        </button>
        <button
          className={`platform-tab-btn ${activeTab === 'gpm' ? 'active' : ''}`}
          onClick={() => setActiveTab('gpm')}
        >
          <span>🛡️ Trung tâm Profile GPM & Ma trận</span>
          <span className="platform-tab-badge">{gpmProfiles.length}</span>
        </button>
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

      {/* TAB 1: YOUTUBE */}
      {activeTab === 'youtube' && (
        <div className="tab-content-pane">
          {/* GPM-Login Quick Status Card */}
          <div className="result-panel" style={{ padding: 16, border: '1px solid #2a4365', background: 'rgba(30, 58, 138, 0.18)' }}>
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
          </div>

          {/* 1-Click YouTube Scanner Card */}
          <div className="scanner-hero-card youtube-theme">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
              <div>
                <h3 style={{ margin: '0 0 4px 0', color: '#fff', fontSize: '1.2rem', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span>📺 Kết nối Kênh YouTube 1-Click (Zero-API)</span>
                  <span className="scanner-header-badge">Tự động hóa CDP</span>
                </h3>
                <p style={{ margin: 0, color: '#cbd5e1', fontSize: '0.88rem' }}>
                  Hỗ trợ cả <strong>Profile GPM-Login</strong> lẫn <strong>Trình duyệt Cốc Cốc / Chrome Local</strong>. Không cần Google Cloud API hay OAuth Secret.
                </p>
              </div>
            </div>

            <div className="scanner-steps-grid">
              <div className="scanner-step-box">
                <div className="scanner-step-num"><span>1</span> Chọn Profile Trình duyệt</div>
                {renderUnifiedProfileSelect(
                  scannerProfileId,
                  e => setScannerProfileId(e.target.value),
                  'youtube',
                  '-- Chọn Cốc Cốc / Chrome / GPM --'
                )}
              </div>

              <div className="scanner-step-box">
                <div className="scanner-step-num"><span>2</span> Mở Trình duyệt</div>
                <button
                  className="btn-run"
                  type="button"
                  disabled={gpmBusy || !scannerProfileId || scanningPlatform !== null}
                  onClick={() => openPlatformBrowserHandler(scannerProfileId, 'youtube')}
                  style={{ height: '38px', fontWeight: 'bold' }}
                >
                  🚀 Mở YouTube Studio
                </button>
                <span style={{ fontSize: '11px', color: '#94a3b8' }}>Đăng nhập tài khoản / kênh trên cửa sổ vừa mở nếu chưa đăng nhập.</span>
              </div>

              <div className="scanner-step-box">
                <div className="scanner-step-num"><span>3</span> Tự động Nhận diện</div>
                <button
                  className="btn-save"
                  type="button"
                  disabled={gpmBusy || !scannerProfileId || scanningPlatform !== null}
                  onClick={() => scanYouTubeHandler(scannerProfileId)}
                  style={{ height: '38px', fontWeight: 'bold', background: scanningPlatform === 'youtube' ? '#6b7280' : '#10b981' }}
                >
                  {scanningPlatform === 'youtube' ? '⏳ Đang quét kênh...' : '🔍 Quét & Liên kết Kênh'}
                </button>
                <span style={{ fontSize: '11px', color: '#94a3b8' }}>Hệ thống tự đọc Tên, Channel ID, Avatar và lưu kích hoạt ngay.</span>
              </div>
            </div>
          </div>

          {/* Collapsible Advanced OAuth Section */}
          <div className="collapsible-accordion">
            <div
              className="collapsible-accordion-header"
              onClick={() => setShowAdvancedOAuth(!showAdvancedOAuth)}
            >
              <span>⚙️ Cấu hình Google OAuth nâng cao (Dành cho nhà phát triển gọi REST API)</span>
              <span>{showAdvancedOAuth ? '▲ Thu gọn' : '▼ Mở rộng'}</span>
            </div>

            {showAdvancedOAuth && (
              <div className="collapsible-accordion-body">
                <div style={{ display: 'grid', gap: 10 }}>
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
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 6 }}>
                    <button className="btn-save" disabled={busy} onClick={saveConfig}>💾 Lưu OAuth Client</button>
                    <button
                      className="btn-run"
                      disabled={busy || !config.client_id || !config.client_secret_configured}
                      onClick={() => connectChannel({ gpmProfileId: newChannelGpmProfileId })}
                    >
                      ➕ Kết nối qua Google OAuth
                    </button>
                    <button className="btn-secondary" disabled={busy} onClick={load}>↻ Làm mới</button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Prompt Selection Support */}
          {promptEntries.length > 0 && (
            <div className="result-panel" style={{ padding: 16 }}>
              <label style={{ display: 'block', marginBottom: 8 }}>
                Chọn bộ prompt để cài đặt kênh:
              </label>
              <select
                className="version-select"
                style={{ width: '100%' }}
                value={selectedPromptVersion}
                onChange={event => {
                  setSelectedPromptVersion(event.target.value)
                  setSelectedChannelIdOverride('')
                }}
              >
                {promptEntries.map(([versionId, version]) => (
                  <option key={versionId} value={versionId}>
                    {version.name || versionId}
                  </option>
                ))}
              </select>
              {selectedPrompt && !promptDefaultChannelId && (
                <div className="help-text" style={{ marginTop: 8, color: '#f5b041' }}>
                  Bộ prompt này chưa được chọn kênh YouTube mặc định. Bạn có thể gán kênh mặc định trong Settings.
                </div>
              )}
            </div>
          )}

          {/* Channel Switcher Tabs */}
          {channels.length > 1 && (
            <div className="result-panel" style={{ padding: 14 }}>
              <strong>📺 Chuyển nhanh giữa các kênh ({channels.length} kênh):</strong>
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
            <div key={channel.id} className="result-panel" style={{ padding: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                {channel.thumbnail_url && <img src={channel.thumbnail_url} alt="" style={{ width: 42, height: 42, borderRadius: '50%' }} />}
                <div style={{ flex: 1, minWidth: 200 }}>
                  <strong style={{ fontSize: '1.1rem', color: '#fff' }}>{channel.title}</strong>
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
                        {getProfileDropdownLabel(p, 'youtube')}
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

              {/* Reconnect with OAuth client */}
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

              {/* Comment Automation Settings */}
              <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid #334155' }}>
                <strong style={{ color: '#60a5fa' }}>💬 Tự động hóa bình luận YouTube</strong>
                <div style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1fr) 170px', gap: 10, marginTop: 10 }}>
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
                style={{ minHeight: 90, marginTop: 14 }}
                value={channel.reply_instruction || ''}
                onChange={event => changeChannel(channel.id, 'reply_instruction', event.target.value)}
                placeholder="Phong cách trả lời riêng của kênh (không bắt buộc)"
              />
              <div style={{ marginTop: 12 }}>
                <button className="btn-save" disabled={busy} onClick={() => saveChannel(channel)}>💾 Lưu kênh</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* TAB 2: FACEBOOK */}
      {activeTab === 'facebook' && (
        <div className="tab-content-pane">
          {/* Facebook Intro Banner */}
          <div className="result-panel" style={{ padding: 18, border: '1px solid rgba(59, 130, 246, 0.4)', background: 'rgba(30, 58, 138, 0.15)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
              <div>
                <h3 style={{ margin: '0 0 6px 0', color: '#60a5fa', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span>📘 Quản lý Kênh / Fanpage Facebook</span>
                  <span style={{ fontSize: '11px', background: '#2563eb', color: '#fff', padding: '2px 8px', borderRadius: 10 }}>Sẵn sàng mở rộng</span>
                </h3>
                <p style={{ margin: 0, color: '#94a3b8', fontSize: '0.9rem' }}>
                  Kết nối Fanpage hoặc Nhóm Facebook với Profile GPM-Login. Cho phép dùng chung Profile GPM với kênh YouTube/TikTok cùng hệ sinh thái mà không lo xung đột IP.
                </p>
              </div>
              <button
                className="btn-run"
                onClick={() => setIsAddingFbPage(!isAddingFbPage)}
              >
                {isAddingFbPage ? '✕ Đóng form' : '➕ Kết nối Fanpage mới'}
              </button>
            </div>
          </div>

          {/* 1-Click Facebook Scanner Card */}
          <div className="scanner-hero-card facebook-theme">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
              <div>
                <h3 style={{ margin: '0 0 4px 0', color: '#fff', fontSize: '1.2rem', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span>📘 Kết nối Fanpage Facebook 1-Click</span>
                  <span className="scanner-header-badge">Tự động hóa CDP</span>
                </h3>
                <p style={{ margin: 0, color: '#cbd5e1', fontSize: '0.88rem' }}>
                  Hỗ trợ cả <strong>Trình duyệt Cốc Cốc / Chrome Local</strong> (đang đăng nhập sẵn) lẫn <strong>Profile GPM-Login</strong>.
                </p>
              </div>
            </div>

            <div className="scanner-steps-grid">
              <div className="scanner-step-box">
                <div className="scanner-step-num"><span>1</span> Chọn Profile Trình duyệt</div>
                {renderUnifiedProfileSelect(
                  newFbPage.gpm_profile_id,
                  e => setNewFbPage({ ...newFbPage, gpm_profile_id: e.target.value }),
                  'facebook',
                  '-- Chọn Cốc Cốc / Chrome / GPM --'
                )}
              </div>

              <div className="scanner-step-box">
                <div className="scanner-step-num"><span>2</span> Mở Trình duyệt</div>
                <button
                  className="btn-run"
                  type="button"
                  disabled={gpmBusy || !newFbPage.gpm_profile_id || scanningPlatform !== null}
                  onClick={() => openPlatformBrowserHandler(newFbPage.gpm_profile_id, 'facebook')}
                  style={{ height: '38px', fontWeight: 'bold' }}
                >
                  🚀 Mở Facebook / Business Suite
                </button>
                <span style={{ fontSize: '11px', color: '#94a3b8' }}>Mở Cốc Cốc hoặc GPM để kiểm tra hoặc đăng nhập Facebook nếu cần.</span>
              </div>

              <div className="scanner-step-box">
                <div className="scanner-step-num"><span>3</span> Tự động Quét Fanpage</div>
                <button
                  className="btn-save"
                  type="button"
                  disabled={gpmBusy || !newFbPage.gpm_profile_id || scanningPlatform !== null}
                  onClick={() => scanFacebookHandler(newFbPage.gpm_profile_id)}
                  style={{ height: '38px', fontWeight: 'bold', background: scanningPlatform === 'facebook' ? '#6b7280' : '#3b82f6' }}
                >
                  {scanningPlatform === 'facebook' ? '⏳ Đang quét Facebook...' : '🔍 Quét Danh sách Fanpage'}
                </button>
                <span style={{ fontSize: '11px', color: '#94a3b8' }}>Tự động đọc toàn bộ Page bạn đang quản lý mà không cần copy token.</span>
              </div>
            </div>

            {/* Discovered Pages List */}
            {discoveredFbPages.length > 0 && (
              <div className="discovered-pages-container">
                <strong style={{ color: '#34d399', fontSize: '1rem', display: 'flex', alignItems: 'center', gap: 6 }}>
                  ✨ Tìm thấy {discoveredFbPages.length} Fanpage trong trình duyệt:
                </strong>
                <div style={{ marginTop: 10, display: 'grid', gap: 8 }}>
                  {discoveredFbPages.map(page => (
                    <div key={page.page_id} className="discovered-page-item">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        {page.avatar_url ? (
                          <img src={page.avatar_url} alt="" className="discovered-page-avatar" />
                        ) : (
                          <div className="discovered-page-avatar" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontWeight: 'bold' }}>f</div>
                        )}
                        <div>
                          <strong style={{ color: '#fff' }}>{page.name}</strong>
                          <div style={{ color: '#94a3b8', fontSize: '0.8rem' }}>ID: {page.page_id}</div>
                        </div>
                      </div>
                      <button
                        className="btn-save"
                        type="button"
                        style={{ padding: '6px 14px', fontSize: '12px' }}
                        onClick={() => linkDiscoveredFbPageHandler(page)}
                      >
                        ➕ Liên kết Fanpage này
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Collapsible Manual Add Fanpage Form */}
          <div className="collapsible-accordion">
            <div
              className="collapsible-accordion-header"
              onClick={() => setShowManualFb(!showManualFb)}
            >
              <span>⚙️ Thêm Fanpage thủ công (Nhập Page ID & Meta Access Token)</span>
              <span>{showManualFb ? '▲ Thu gọn' : '▼ Mở rộng'}</span>
            </div>

            {showManualFb && (
              <div className="collapsible-accordion-body">
                <div style={{ display: 'grid', gap: 12 }}>
                  <div>
                    <label style={{ display: 'block', marginBottom: 4, color: '#e2e8f0', fontSize: '0.9rem' }}>
                      Tên Fanpage / Kênh Facebook: <span style={{ color: '#ef4444' }}>*</span>
                    </label>
                    <input
                      className="version-select"
                      style={{ width: '100%' }}
                      value={newFbPage.name}
                      onChange={e => setNewFbPage({ ...newFbPage, name: e.target.value })}
                      placeholder="Ví dụ: TechReview Official, Góc Giải Trí..."
                    />
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
                    <div>
                      <label style={{ display: 'block', marginBottom: 4, color: '#e2e8f0', fontSize: '0.9rem' }}>
                        Facebook Page ID / Username:
                      </label>
                      <input
                        className="version-select"
                        style={{ width: '100%' }}
                        value={newFbPage.page_id}
                        onChange={e => setNewFbPage({ ...newFbPage, page_id: e.target.value })}
                        placeholder="Ví dụ: 1098234857283 hoặc techreview.vn"
                      />
                    </div>

                    <div>
                      <label style={{ display: 'block', marginBottom: 4, color: '#e2e8f0', fontSize: '0.9rem' }}>
                        🛡️ Gán Profile Trình duyệt:
                      </label>
                      {renderUnifiedProfileSelect(
                        newFbPage.gpm_profile_id,
                        e => setNewFbPage({ ...newFbPage, gpm_profile_id: e.target.value }),
                        'facebook'
                      )}
                    </div>
                  </div>

                  <div>
                    <label style={{ display: 'block', marginBottom: 4, color: '#e2e8f0', fontSize: '0.9rem' }}>
                      Page Access Token / Meta API Secret (Tùy chọn, phục vụ tự động hóa Graph API):
                    </label>
                    <input
                      className="version-select"
                      style={{ width: '100%' }}
                      type="password"
                      value={newFbPage.access_token}
                      onChange={e => setNewFbPage({ ...newFbPage, access_token: e.target.value })}
                      placeholder="EAA..."
                    />
                  </div>

                  <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginTop: 4 }}>
                    <label style={{ color: '#e2e8f0', fontSize: '0.9rem', display: 'flex', alignItems: 'center', gap: 6 }}>
                      <input
                        type="checkbox"
                        checked={newFbPage.auto_reels}
                        onChange={e => setNewFbPage({ ...newFbPage, auto_reels: e.target.checked })}
                      /> Tự động đăng Reels / Video ngắn
                    </label>
                    <label style={{ color: '#e2e8f0', fontSize: '0.9rem', display: 'flex', alignItems: 'center', gap: 6 }}>
                      <input
                        type="checkbox"
                        checked={newFbPage.auto_comment}
                        onChange={e => setNewFbPage({ ...newFbPage, auto_comment: e.target.checked })}
                      /> Tự động trả lời bình luận
                    </label>
                  </div>

                  <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
                    <button className="btn-save" onClick={handleAddFacebookPage}>
                      💾 Lưu và Liên Kết Fanpage
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Facebook Pages List */}
          {facebookPages.length === 0 ? (
            <div className="result-panel" style={{ textAlign: 'center', padding: 32, color: '#94a3b8' }}>
              <div style={{ fontSize: '2.5rem', marginBottom: 8 }}>📘</div>
              <p>Chưa có Fanpage Facebook nào được liên kết.</p>
              <button className="btn-run" onClick={() => setIsAddingFbPage(true)} style={{ marginTop: 8 }}>
                ➕ Thêm Fanpage Đầu Tiên
              </button>
            </div>
          ) : (
            <div className="fb-card-grid">
              {facebookPages.map(page => {
                const usage = getProfileUsage(page.gpm_profile_id)
                return (
                  <div key={page.id} className="fb-card">
                    <div className="fb-card-header">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div className="fb-card-avatar">f</div>
                        <div>
                          <strong style={{ color: '#fff', fontSize: '1.05rem' }}>{page.name}</strong>
                          <div style={{ color: '#94a3b8', fontSize: '0.8rem' }}>ID: {page.page_id}</div>
                        </div>
                      </div>
                      <button
                        className="btn-danger"
                        style={{ padding: '4px 8px', fontSize: '11px' }}
                        onClick={() => handleDeleteFacebookPage(page.id)}
                        title="Xóa liên kết"
                      >
                        🗑️
                      </button>
                    </div>

                    <div style={{ fontSize: '0.85rem', color: '#cbd5e1', display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <div>
                        🛡️ <strong>GPM Profile:</strong> {page.gpm_profile_name || page.gpm_profile_id || 'Chưa gán'}
                      </div>
                      {page.gpm_proxy_info && (
                        <div style={{ color: '#34d399', fontSize: '0.8rem' }}>
                          🌐 Proxy: {page.gpm_proxy_info}
                        </div>
                      )}
                      {usage.statusType === 'shared-safe' && (
                        <div style={{ marginTop: 4 }}>
                          <span className="safety-pill shared-safe" style={{ fontSize: '11px' }}>
                            {usage.statusText}
                          </span>
                        </div>
                      )}
                    </div>

                    <div className="fb-card-actions">
                      <button
                        className="btn-run"
                        type="button"
                        disabled={gpmBusy || !page.gpm_profile_id}
                        onClick={() => openFacebookInGpm(page.gpm_profile_id)}
                        title="Mở Facebook Business Suite trong Profile GPM"
                      >
                        🚀 Mở Facebook GPM
                      </button>
                      <button
                        className="btn-secondary"
                        type="button"
                        disabled={gpmBusy || !page.gpm_profile_id}
                        onClick={() => stopGpmProfileHandler(page.gpm_profile_id)}
                      >
                        ⏹ Đóng Profile
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* TAB 3: TIKTOK */}
      {activeTab === 'tiktok' && (
        <div className="tab-content-pane">
          {/* TikTok Intro Banner */}
          <div className="result-panel" style={{ padding: 18, border: '1px solid rgba(254, 44, 85, 0.4)', background: 'rgba(254, 44, 85, 0.08)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
              <div>
                <h3 style={{ margin: '0 0 6px 0', color: '#fe2c55', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span>🎵 Quản lý Kênh / Tài khoản TikTok</span>
                  <span style={{ fontSize: '11px', background: '#25f4ee', color: '#000', padding: '2px 8px', borderRadius: 10, fontWeight: 'bold' }}>Sẵn sàng mở rộng</span>
                </h3>
                <p style={{ margin: 0, color: '#94a3b8', fontSize: '0.9rem' }}>
                  Quản lý Tài khoản TikTok sáng tạo video ngắn / TikTok Creator. Gán chung Profile GPM với kênh YouTube & Fanpage Facebook cùng thương hiệu an toàn tuyệt đối.
                </p>
              </div>
              <button
                className="btn-run"
                onClick={() => setIsAddingTiktok(!isAddingTiktok)}
                style={{ background: 'linear-gradient(135deg, #fe2c55, #25f4ee)', color: '#000', fontWeight: 'bold' }}
              >
                {isAddingTiktok ? '✕ Đóng form' : '➕ Kết nối Kênh TikTok mới'}
              </button>
            </div>
          </div>

          {/* 1-Click TikTok Scanner Card */}
          <div className="scanner-hero-card tiktok-theme">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
              <div>
                <h3 style={{ margin: '0 0 4px 0', color: '#fff', fontSize: '1.2rem', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span>🎵 Kết nối Kênh TikTok 1-Click</span>
                  <span className="scanner-header-badge" style={{ borderColor: '#fe2c55', color: '#fe2c55', background: 'rgba(254, 44, 85, 0.15)' }}>Tự động hóa CDP</span>
                </h3>
                <p style={{ margin: 0, color: '#cbd5e1', fontSize: '0.88rem' }}>
                  Hỗ trợ cả <strong>Trình duyệt Cốc Cốc / Chrome Local</strong> (đang đăng nhập sẵn) lẫn <strong>Profile GPM-Login</strong>.
                </p>
              </div>
            </div>

            <div className="scanner-steps-grid">
              <div className="scanner-step-box">
                <div className="scanner-step-num"><span>1</span> Chọn Profile Trình duyệt</div>
                {renderUnifiedProfileSelect(
                  newTiktok.gpm_profile_id,
                  e => setNewTiktok({ ...newTiktok, gpm_profile_id: e.target.value }),
                  'tiktok',
                  '-- Chọn Cốc Cốc / Chrome / GPM --'
                )}
              </div>

              <div className="scanner-step-box">
                <div className="scanner-step-num"><span>2</span> Mở Trình duyệt</div>
                <button
                  className="btn-run"
                  type="button"
                  disabled={gpmBusy || !newTiktok.gpm_profile_id || scanningPlatform !== null}
                  onClick={() => openPlatformBrowserHandler(newTiktok.gpm_profile_id, 'tiktok')}
                  style={{ height: '38px', fontWeight: 'bold' }}
                >
                  🚀 Mở TikTok Creator
                </button>
                <span style={{ fontSize: '11px', color: '#94a3b8' }}>Mở Cốc Cốc hoặc GPM để đăng nhập TikTok Creator nếu cần.</span>
              </div>

              <div className="scanner-step-box">
                <div className="scanner-step-num"><span>3</span> Tự động Quét Kênh</div>
                <button
                  className="btn-save"
                  type="button"
                  disabled={gpmBusy || !newTiktok.gpm_profile_id || scanningPlatform !== null}
                  onClick={() => scanTiktokHandler(newTiktok.gpm_profile_id)}
                  style={{ height: '38px', fontWeight: 'bold', background: scanningPlatform === 'tiktok' ? '#6b7280' : 'linear-gradient(135deg, #fe2c55, #25f4ee)', color: '#000' }}
                >
                  {scanningPlatform === 'tiktok' ? '⏳ Đang quét TikTok...' : '🔍 Quét Kênh TikTok'}
                </button>
                <span style={{ fontSize: '11px', color: '#94a3b8' }}>Tự động đọc Handle (@username), Tên hiển thị và Avatar.</span>
              </div>
            </div>

            {/* Discovered TikTok Account Card */}
            {discoveredTiktok && (
              <div className="discovered-pages-container" style={{ borderColor: '#fe2c55' }}>
                <strong style={{ color: '#25f4ee', fontSize: '1rem', display: 'flex', alignItems: 'center', gap: 6 }}>
                  ✨ Tìm thấy Kênh TikTok trong trình duyệt:
                </strong>
                <div className="discovered-page-item" style={{ marginTop: 10 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    {discoveredTiktok.avatar_url ? (
                      <img src={discoveredTiktok.avatar_url} alt="" className="discovered-page-avatar" />
                    ) : (
                      <div className="discovered-page-avatar" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontWeight: 'bold', background: '#fe2c55' }}>🎵</div>
                    )}
                    <div>
                      <strong style={{ color: '#fff' }}>{discoveredTiktok.name}</strong>
                      <div style={{ color: '#25f4ee', fontSize: '0.85rem' }}>{discoveredTiktok.handle}</div>
                    </div>
                  </div>
                  <button
                    className="btn-save"
                    type="button"
                    style={{ padding: '6px 14px', fontSize: '12px' }}
                    onClick={() => linkDiscoveredTiktokHandler(discoveredTiktok)}
                  >
                    ➕ Liên kết Kênh này
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Collapsible Manual Add TikTok Form */}
          <div className="collapsible-accordion">
            <div
              className="collapsible-accordion-header"
              onClick={() => setShowManualTiktok(!showManualTiktok)}
            >
              <span>⚙️ Thêm Kênh TikTok thủ công (Nhập Username & Cookie)</span>
              <span>{showManualTiktok ? '▲ Thu gọn' : '▼ Mở rộng'}</span>
            </div>

            {showManualTiktok && (
              <div className="collapsible-accordion-body">
                <div style={{ display: 'grid', gap: 12 }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
                    <div>
                      <label style={{ display: 'block', marginBottom: 4, color: '#e2e8f0', fontSize: '0.9rem' }}>
                        Tên Kênh TikTok: <span style={{ color: '#ef4444' }}>*</span>
                      </label>
                      <input
                        className="version-select"
                        style={{ width: '100%' }}
                        value={newTiktok.name}
                        onChange={e => setNewTiktok({ ...newTiktok, name: e.target.value })}
                        placeholder="Ví dụ: Review Công Nghệ, Kênh Giải Trí..."
                      />
                    </div>

                    <div>
                      <label style={{ display: 'block', marginBottom: 4, color: '#e2e8f0', fontSize: '0.9rem' }}>
                        TikTok Username / Handle: <span style={{ color: '#ef4444' }}>*</span>
                      </label>
                      <input
                        className="version-select"
                        style={{ width: '100%' }}
                        value={newTiktok.handle}
                        onChange={e => setNewTiktok({ ...newTiktok, handle: e.target.value })}
                        placeholder="@techreview_official"
                      />
                    </div>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
                    <div>
                      <label style={{ display: 'block', marginBottom: 4, color: '#e2e8f0', fontSize: '0.9rem' }}>
                        🛡️ Gán Profile Trình duyệt:
                      </label>
                      {renderUnifiedProfileSelect(
                        newTiktok.gpm_profile_id,
                        e => setNewTiktok({ ...newTiktok, gpm_profile_id: e.target.value }),
                        'tiktok'
                      )}
                    </div>

                    <div>
                      <label style={{ display: 'block', marginBottom: 4, color: '#e2e8f0', fontSize: '0.9rem' }}>
                        Session ID / Cookie Auth (Tùy chọn):
                      </label>
                      <input
                        className="version-select"
                        style={{ width: '100%' }}
                        type="password"
                        value={newTiktok.session_id}
                        onChange={e => setNewTiktok({ ...newTiktok, session_id: e.target.value })}
                        placeholder="sessionid=..."
                      />
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginTop: 4 }}>
                    <label style={{ color: '#e2e8f0', fontSize: '0.9rem', display: 'flex', alignItems: 'center', gap: 6 }}>
                      <input
                        type="checkbox"
                        checked={newTiktok.auto_video}
                        onChange={e => setNewTiktok({ ...newTiktok, auto_video: e.target.checked })}
                      /> Tự động xuất video &amp; đăng lên TikTok
                    </label>
                    <label style={{ color: '#e2e8f0', fontSize: '0.9rem', display: 'flex', alignItems: 'center', gap: 6 }}>
                      <input
                        type="checkbox"
                        checked={newTiktok.auto_comment}
                        onChange={e => setNewTiktok({ ...newTiktok, auto_comment: e.target.checked })}
                      /> Tự động phản hồi bình luận TikTok
                    </label>
                  </div>

                  <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
                    <button className="btn-save" onClick={handleAddTiktok}>
                      💾 Lưu và Liên Kết Kênh TikTok
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* TikTok Accounts List */}
          {tiktokAccounts.length === 0 ? (
            <div className="result-panel" style={{ textAlign: 'center', padding: 32, color: '#94a3b8' }}>
              <div style={{ fontSize: '2.5rem', marginBottom: 8 }}>🎵</div>
              <p>Chưa có Kênh TikTok nào được liên kết.</p>
              <button
                className="btn-run"
                onClick={() => setIsAddingTiktok(true)}
                style={{ marginTop: 8, background: 'linear-gradient(135deg, #fe2c55, #25f4ee)', color: '#000', fontWeight: 'bold' }}
              >
                ➕ Thêm Kênh TikTok Đầu Tiên
              </button>
            </div>
          ) : (
            <div className="tt-card-grid">
              {tiktokAccounts.map(account => {
                const usage = getProfileUsage(account.gpm_profile_id)
                return (
                  <div key={account.id} className="tt-card">
                    <div className="tt-card-header">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div className="tt-card-avatar">♬</div>
                        <div>
                          <strong style={{ color: '#fff', fontSize: '1.05rem' }}>{account.name}</strong>
                          <div style={{ color: '#25f4ee', fontSize: '0.85rem', fontWeight: 600 }}>{account.handle}</div>
                        </div>
                      </div>
                      <button
                        className="btn-danger"
                        style={{ padding: '4px 8px', fontSize: '11px' }}
                        onClick={() => handleDeleteTiktok(account.id)}
                        title="Xóa liên kết"
                      >
                        🗑️
                      </button>
                    </div>

                    <div style={{ fontSize: '0.85rem', color: '#cbd5e1', display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <div>
                        🛡️ <strong>GPM Profile:</strong> {account.gpm_profile_name || account.gpm_profile_id || 'Chưa gán'}
                      </div>
                      {account.gpm_proxy_info && (
                        <div style={{ color: '#34d399', fontSize: '0.8rem' }}>
                          🌐 Proxy: {account.gpm_proxy_info}
                        </div>
                      )}
                      {usage.statusType === 'shared-safe' && (
                        <div style={{ marginTop: 4 }}>
                          <span className="safety-pill shared-safe" style={{ fontSize: '11px' }}>
                            {usage.statusText}
                          </span>
                        </div>
                      )}
                    </div>

                    <div className="tt-card-actions">
                      <button
                        className="btn-run"
                        type="button"
                        disabled={gpmBusy || !account.gpm_profile_id}
                        onClick={() => openTiktokInGpm(account.gpm_profile_id, account.handle)}
                        title="Mở TikTok Creator Studio trong Profile GPM"
                      >
                        🚀 Mở TikTok GPM
                      </button>
                      <button
                        className="btn-secondary"
                        type="button"
                        disabled={gpmBusy || !account.gpm_profile_id}
                        onClick={() => stopGpmProfileHandler(account.gpm_profile_id)}
                      >
                        ⏹ Đóng Profile
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* TAB 4: GPM CENTER & ISOLATION MATRIX */}
      {activeTab === 'gpm' && (
        <div className="tab-content-pane">
          {/* GPM Server Config Panel */}
          <div className="result-panel" style={{ padding: 18, border: '1px solid #2a4365', background: 'rgba(30, 58, 138, 0.18)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: '1.4rem' }}>🛡️</span>
                <strong style={{ fontSize: '1.1rem' }}>Cấu hình GPM-Login v3 Local API</strong>
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
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 1fr) auto auto', gap: 10, marginTop: 14 }}>
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
            <div className="help-text" style={{ marginTop: 8, color: '#93c5fd' }}>
              GPM-Login v3 Local API mặc định chạy ở <code>http://127.0.0.1:19995</code>.
            </div>
          </div>

          {/* Profile Search and Matrix Header */}
          <div className="result-panel" style={{ padding: 18 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 14 }}>
              <div>
                <h3 style={{ margin: '0 0 4px 0', color: '#fff', fontSize: '1.15rem' }}>
                  📊 Ma Trận Phân Bổ Profile GPM & An Toàn Đa Nền Tảng
                </h3>
                <p style={{ margin: 0, color: '#94a3b8', fontSize: '0.85rem' }}>
                  Theo dõi danh sách Profile GPM, Proxy IP tương ứng và các kênh được gán trên từng nền tảng (YouTube, Facebook, TikTok).
                </p>
              </div>
              <input
                className="version-select"
                style={{ minWidth: 260 }}
                placeholder="🔍 Tìm theo tên profile, proxy IP, ID..."
                value={gpmSearchFilter}
                onChange={e => setGpmSearchFilter(e.target.value)}
              />
            </div>

            {/* Matrix Table */}
            <div className="gpm-matrix-table-wrapper">
              <table className="gpm-matrix-table">
                <thead>
                  <tr>
                    <th>Tên Profile & ID</th>
                    <th>Proxy IP</th>
                    <th>Kênh YouTube</th>
                    <th>Kênh Facebook</th>
                    <th>Kênh TikTok</th>
                    <th>Trạng Thái Phân Bổ</th>
                    <th style={{ textAlign: 'right' }}>Thao Tác</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredGpmProfiles.length === 0 ? (
                    <tr>
                      <td colSpan={7} style={{ textAlign: 'center', padding: 24, color: '#94a3b8' }}>
                        {gpmStatus.online ? 'Không tìm thấy Profile nào phù hợp với bộ lọc.' : 'GPM-Login đang Offline. Vui lòng bật ứng dụng GPM-Login và kiểm tra kết nối.'}
                      </td>
                    </tr>
                  ) : (
                    filteredGpmProfiles.map(profile => {
                      const usage = getProfileUsage(profile.id)
                      return (
                        <tr key={profile.id}>
                          <td>
                            <strong style={{ color: '#fff', display: 'block' }}>{profile.name}</strong>
                            <span style={{ fontSize: '0.75rem', color: '#64748b' }}>{profile.id}</span>
                          </td>
                          <td>
                            {profile.proxy_display ? (
                              <span style={{ color: '#34d399', fontSize: '0.85rem', fontFamily: 'monospace' }}>
                                {profile.proxy_display}
                              </span>
                            ) : (
                              <span style={{ color: '#94a3b8', fontSize: '0.8rem' }}>Direct (No Proxy)</span>
                            )}
                          </td>
                          <td>
                            {usage.ytAssigned.length > 0 ? (
                              usage.ytAssigned.map(ch => (
                                <div key={ch.id} style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '2px 0' }}>
                                  <span style={{ color: '#ef4444' }}>📺</span>
                                  <span style={{ fontWeight: 500 }}>{ch.title}</span>
                                </div>
                              ))
                            ) : (
                              <span style={{ color: '#64748b', fontSize: '0.8rem' }}>—</span>
                            )}
                          </td>
                          <td>
                            {usage.fbAssigned.length > 0 ? (
                              usage.fbAssigned.map(fb => (
                                <div key={fb.id} style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '2px 0' }}>
                                  <span style={{ color: '#3b82f6' }}>📘</span>
                                  <span style={{ fontWeight: 500 }}>{fb.name}</span>
                                </div>
                              ))
                            ) : (
                              <span style={{ color: '#64748b', fontSize: '0.8rem' }}>—</span>
                            )}
                          </td>
                          <td>
                            {usage.ttAssigned.length > 0 ? (
                              usage.ttAssigned.map(tt => (
                                <div key={tt.id} style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '2px 0' }}>
                                  <span style={{ color: '#25f4ee' }}>🎵</span>
                                  <span style={{ fontWeight: 500 }}>{tt.handle}</span>
                                </div>
                              ))
                            ) : (
                              <span style={{ color: '#64748b', fontSize: '0.8rem' }}>—</span>
                            )}
                          </td>
                          <td>
                            <span className={`safety-pill ${usage.statusType}`} title={usage.tooltip}>
                              {usage.statusText}
                            </span>
                          </td>
                          <td style={{ textAlign: 'right' }}>
                            <div style={{ display: 'inline-flex', gap: 6 }}>
                              <button
                                className="btn-run"
                                style={{ padding: '4px 10px', fontSize: '11px' }}
                                disabled={gpmBusy}
                                onClick={() => startGpmProfileHandler(profile.id)}
                                title="Mở Profile trên GPM"
                              >
                                🚀 Mở
                              </button>
                              <button
                                className="btn-secondary"
                                style={{ padding: '4px 10px', fontSize: '11px' }}
                                disabled={gpmBusy}
                                onClick={() => stopGpmProfileHandler(profile.id)}
                                title="Đóng Profile trên GPM"
                              >
                                ⏹ Đóng
                              </button>
                            </div>
                          </td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
