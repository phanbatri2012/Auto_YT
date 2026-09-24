import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const channelManagerSource = readFileSync(
  new URL('./ChannelManager.jsx', import.meta.url),
  'utf8'
)

test('ChannelManager exposes Local Browser profiles and Scanner APIs', () => {
  assert.match(channelManagerSource, /api\/channels\/local-profiles/)
  assert.match(channelManagerSource, /api\/channels\/open-browser/)
  assert.match(channelManagerSource, /api\/channels\/scan\/youtube/)
  assert.match(channelManagerSource, /api\/channels\/scan\/facebook/)
  assert.match(channelManagerSource, /api\/channels\/scan\/tiktok/)
})

test('ChannelManager exposes 1-Click Zero-API YouTube Scanner Card and Collapsible OAuth', () => {
  assert.match(channelManagerSource, /Kết nối Kênh YouTube 1-Click/)
  assert.match(channelManagerSource, /Mở YouTube Studio/)
  assert.match(channelManagerSource, /Quét & Liên kết Kênh/)
  assert.match(channelManagerSource, /showAdvancedOAuth/)
})

test('ChannelManager exposes 1-Click Facebook Scanner Card with Discovered Pages', () => {
  assert.match(channelManagerSource, /Kết nối Fanpage Facebook 1-Click/)
  assert.match(channelManagerSource, /Mở Facebook \/ Business Suite/)
  assert.match(channelManagerSource, /Quét Danh sách Fanpage/)
  assert.match(channelManagerSource, /discoveredFbPages/)
  assert.match(channelManagerSource, /linkDiscoveredFbPageHandler/)
})

test('ChannelManager exposes 1-Click TikTok Scanner Card with Discovered Account', () => {
  assert.match(channelManagerSource, /Kết nối Kênh TikTok 1-Click/)
  assert.match(channelManagerSource, /Mở TikTok Creator/)
  assert.match(channelManagerSource, /Quét Kênh TikTok/)
  assert.match(channelManagerSource, /discoveredTiktok/)
  assert.match(channelManagerSource, /linkDiscoveredTiktokHandler/)
})

test('ChannelManager renders Unified Profile options grouping Local (Cốc Cốc) and GPM profiles', () => {
  assert.match(channelManagerSource, /renderUnifiedProfileSelect/)
  assert.match(channelManagerSource, /Trình duyệt Cốc Cốc \/ Local/)
  assert.match(channelManagerSource, /Profiles GPM-Login/)
})

test('ChannelManager and YouTubeChannelSettings preserve unsaved form inputs on window focus', () => {
  const ytSettingsSource = readFileSync(
    new URL('./YouTubeChannelSettings.jsx', import.meta.url),
    'utf8'
  )

  // Verify ChannelManager protects in-progress typing
  assert.match(channelManagerSource, /onFocus = \(\) => load\(\{ isFocus: true \}\)/)
  assert.match(channelManagerSource, /client_secret: previous\.client_secret/)
  assert.match(channelManagerSource, /if \(!isFocus\)\s*\{\s*setGpmConfig\(configData\)/)

  // Verify YouTubeChannelSettings protects in-progress typing
  assert.match(ytSettingsSource, /onFocus = \(\) => load\(\{ isFocus: true \}\)/)
  assert.match(ytSettingsSource, /client_secret: previous\.client_secret/)
  assert.match(ytSettingsSource, /if \(!isFocus\)\s*\{\s*setGpmConfig\(configData\)/)
})

