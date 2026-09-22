import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const channelSettingsSource = readFileSync(
  new URL('./YouTubeChannelSettings.jsx', import.meta.url),
  'utf8'
)
const channelManagerSource = readFileSync(
  new URL('./ChannelManager.jsx', import.meta.url),
  'utf8'
)

test('YouTubeChannelSettings exposes GPM-Login Local API configuration and health test', () => {
  assert.match(channelSettingsSource, /Cấu hình GPM-Login v3 Local API/)
  assert.match(channelSettingsSource, /api\/gpm\/config/)
  assert.match(channelSettingsSource, /api\/gpm\/status/)
  assert.match(channelSettingsSource, /Kiểm tra kết nối/)
  assert.match(channelSettingsSource, /Lưu cấu hình GPM/)
  assert.match(channelSettingsSource, /127\.0\.0\.1:19995/)
})

test('YouTubeChannelSettings exposes GPM Profile assignment and quick launch controls', () => {
  assert.match(channelSettingsSource, /Gán Profile GPM-Login \(Cô lập IP Kênh\)/)
  assert.match(channelSettingsSource, /handleSelectGpmProfile/)
  assert.match(channelSettingsSource, /gpm_profile_id/)
  assert.match(channelSettingsSource, /gpm_profile_name/)
  assert.match(channelSettingsSource, /gpm_proxy_info/)
  assert.match(channelSettingsSource, /Mở Profile/)
  assert.match(channelSettingsSource, /Đóng Profile/)
  assert.match(channelSettingsSource, /api\/gpm\/profiles\/.*start/)
  assert.match(channelSettingsSource, /api\/gpm\/profiles\/.*stop/)
})

test('channel UIs never receive or send raw proxy credentials', () => {
  for (const source of [channelSettingsSource, channelManagerSource]) {
    assert.doesNotMatch(source, /raw_proxy/)
    assert.doesNotMatch(source, /query\.set\(['"]gpm_proxy_info/)
    assert.match(source, /proxy_display/)
  }
})
