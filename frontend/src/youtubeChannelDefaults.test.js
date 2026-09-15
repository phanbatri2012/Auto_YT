import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const commentsSource = readFileSync(
  new URL('./YouTubeComments.jsx', import.meta.url),
  'utf8'
)
const channelSettingsSource = readFileSync(
  new URL('./YouTubeChannelSettings.jsx', import.meta.url),
  'utf8'
)
const settingsSource = readFileSync(
  new URL('./Settings.jsx', import.meta.url),
  'utf8'
)

test('published-link form derives its channel from the selected video prompt', () => {
  assert.match(commentsSource, /default_youtube_channel_db_id/)
  assert.match(commentsSource, /default_youtube_channel_title/)
  assert.match(commentsSource, /Kênh theo bộ prompt/)
  assert.doesNotMatch(commentsSource, /publicationForm\.channelId/)
})

test('published-link request lets the backend resolve the prompt channel', () => {
  const start = commentsSource.indexOf('const addPublication = async')
  const end = commentsSource.indexOf('const loadLegacyVideos = async', start)
  const addPublicationSource = commentsSource.slice(start, end)
  assert.match(commentsSource, /published_url:\s*publicationForm\.url/)
  assert.doesNotMatch(addPublicationSource, /channel_id:\s*Number\(/)
})

test('published-link form allows saving without a connected channel', () => {
  assert.doesNotMatch(commentsSource, /!channels\.length \? \(/)
  assert.match(commentsSource, /Chưa gắn kênh — link chưa được xác minh/)
  assert.match(commentsSource, /Bình luận chưa khả dụng/)
  assert.match(commentsSource, /disabled=\{busy \|\| !publicationForm\.videoId \|\| !publicationForm\.url\.trim\(\)\}/)
  assert.doesNotMatch(commentsSource, /!selectedVideo\?\.default_youtube_channel_db_id \|\| !publicationForm\.url/)
})

test('published-link form only lists videos without an existing publication', () => {
  assert.match(commentsSource, /publications\.map\(publication => Number\(publication\.video_id\)\)/)
  assert.match(commentsSource, /videos\.filter\(video => !linkedVideoIds\.has\(Number\(video\.id\)\)\)/)
  assert.doesNotMatch(commentsSource, /video\.is_published/)
  assert.match(commentsSource, /filteredUnlinkedVideos\.map/)
  assert.match(commentsSource, /api\/video-publications/)
})

test('published-link videos can be narrowed by prompt version', () => {
  assert.match(commentsSource, /publicationPromptFilter/)
  assert.match(commentsSource, /Tất cả bộ prompt/)
  assert.match(commentsSource, /video\.prompt_version/)
  assert.match(commentsSource, /promptVersions\[id\]\?\.name/)
  assert.match(commentsSource, /value=\{version\.id\}>\{version\.name\}/)
  assert.match(commentsSource, /filteredUnlinkedVideos\.map/)
})

test('comments can be filtered independently by channel and video', () => {
  assert.match(commentsSource, /params\.set\('channel_id', channelFilter\)/)
  assert.match(commentsSource, /params\.set\('video_id', videoFilter\)/)
  assert.match(commentsSource, /Tất cả video/)
})

test('comments with a remote reply id are never selectable for another reply', () => {
  assert.match(commentsSource, /comment\.status === 'replied' \|\| Boolean\(comment\.reply_youtube_id\)/)
  assert.match(commentsSource, /disabled=\{isReplied\(comment\) \|\|/)
  assert.match(commentsSource, /'scheduled', 'publishing'/)
})

test('channel settings expose safe automatic reply scheduling controls', () => {
  assert.match(channelSettingsSource, /reply_interval_minutes/)
  assert.match(channelSettingsSource, /quarter_hour_reply_limit/)
  assert.match(channelSettingsSource, /video_half_hour_reply_limit/)
  assert.match(channelSettingsSource, /reply_window_start/)
  assert.match(channelSettingsSource, /reply_paused/)
})

test('legacy video import supports channel-wide selection with exact dedupe states', () => {
  assert.match(commentsSource, /api\/youtube-comments\/import-preview/)
  assert.match(commentsSource, /api\/youtube-comments\/import/)
  assert.match(commentsSource, /Để trống link để tải toàn bộ video của kênh/)
  assert.match(commentsSource, /YouTube Video ID/)
  assert.match(commentsSource, /existing_chat/)
  assert.match(commentsSource, /Chọn tất cả có thể nhập/)
})

test('legacy video import reports request and terminal job outcomes in place', () => {
  assert.match(commentsSource, /const \[importMessage, setImportMessage\]/)
  assert.match(commentsSource, /const \[importTrackedJobIds, setImportTrackedJobIds\]/)
  assert.match(commentsSource, /api\/jobs\/\$\{jobId\}/)
  assert.match(commentsSource, /setImportTrackedJobIds\(trackedJobIds\)/)
  assert.match(commentsSource, /aria-live="polite"/)
  assert.match(commentsSource, /Promise\.allSettled/)
})

test('legacy import only offers prompt sets assigned to the selected channel', () => {
  assert.match(commentsSource, /default_youtube_channel_id/)
  assert.match(commentsSource, /selectedImportChannel\?\.channel_id/)
  assert.match(commentsSource, /Kênh chưa được gắn với bộ prompt trong Settings/)
})

test('channel settings show only the channel assigned to the selected prompt', () => {
  assert.match(channelSettingsSource, /Chọn bộ prompt để cài đặt kênh/)
  assert.match(channelSettingsSource, /selectedPrompt\?\.default_youtube_channel_id/)
  assert.match(channelSettingsSource, /selectedChannel && \[selectedChannel\]\.map/)
  assert.match(settingsSource, /promptVersions=\{promptsData\.versions\}/)
  assert.match(settingsSource, /activePromptVersion=\{activeVersion\}/)
})

test('channel reconnect is locked to its channel and named OAuth client', () => {
  assert.match(channelSettingsSource, /client_name/)
  assert.match(channelSettingsSource, /expected_channel_id/)
  assert.match(channelSettingsSource, /oauth_client_choice: channel\.oauth_client_id \|\| ''/)
  assert.match(channelSettingsSource, /Kết nối lại đúng kênh này/)
  assert.match(channelSettingsSource, /Tên hiện trên màn hình Google là tên ứng dụng OAuth/)
})
