import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'


test('wires the YouTube Downloader into navigation and view rendering', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')

  assert.match(
    appSource,
    /import YouTubeDownloader from ['"]\.\/YouTubeDownloader['"]/
  )
  assert.match(appSource, /activeView === ['"]downloader['"]/)
  assert.match(appSource, />YouTube Downloader<\/li>/)
  assert.match(appSource, /<YouTubeDownloader\s*\/>/)
})


test('wires the persistent Job Center and keeps video submission queueable', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')
  const jobCenterSource = await readFile(new URL('./JobCenter.jsx', import.meta.url), 'utf8')
  const videoQueueSource = await readFile(new URL('./VideoQueuePanel.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /import JobCenter from ['"]\.\/JobCenter['"]/)
  assert.match(appSource, />Trung tâm Job<\/li>/)
  assert.match(appSource, /<JobCenter[\s\S]*onOpenVideo=\{viewSavedVideo\}/)
  assert.match(appSource, /Thêm vào hàng đợi/)
  assert.match(jobCenterSource, /new URLSearchParams/)
  assert.match(jobCenterSource, /params\.set\(['"]search['"], debouncedSearchQuery\)/)
  assert.match(jobCenterSource, /setTimeout[\s\S]*250/)
  assert.match(jobCenterSource, /Tìm theo tiêu đề, link YouTube, mã video hoặc mã job/)
  assert.match(jobCenterSource, /runAction\(job, ['"]retry['"]\)/)
  assert.match(jobCenterSource, /retry_wait: \{ label: ['"]Chờ tự phục hồi['"]/)
  assert.match(jobCenterSource, /job\.resume_from_step/)
  assert.match(jobCenterSource, /job\.next_retry_at/)
  assert.match(jobCenterSource, /const hasPollableJobs = useMemo/)
  assert.match(jobCenterSource, /if \(!hasPollableJobs\) return undefined/)
  assert.match(jobCenterSource, /Chọn tất cả job có thể thao tác/)
  assert.match(jobCenterSource, /selectAllMatching/)
  assert.match(jobCenterSource, /excluded_job_ids/)
  assert.match(jobCenterSource, /snapshot_at: selectionSnapshotAt/)
  assert.match(jobCenterSource, /selectionSummary/)
  assert.match(jobCenterSource, /isWithinSnapshot/)
  assert.match(jobCenterSource, /api\/jobs\/bulk-action/)
  assert.match(jobCenterSource, /BULK_ACTIONS\.map/)
  assert.match(jobCenterSource, /job_type.*typeFilter/)
  assert.match(jobCenterSource, /params\.set\(['"]status['"], filter\)/)
  assert.match(jobCenterSource, /clearSelection\(\)[\s\S]*setTypeFilter/)
  assert.match(videoQueueSource, /'queued', 'running', 'retry_wait', 'paused'/)
  assert.match(videoQueueSource, /const hasPollableJobs = useMemo/)
  assert.match(videoQueueSource, /if \(!hasPollableJobs\) return undefined/)
  assert.match(videoQueueSource, /method: ['"]PATCH['"]/)
  assert.match(videoQueueSource, /method: ['"]DELETE['"]/)
  assert.match(videoQueueSource, />Sửa</)
  assert.match(videoQueueSource, />Xóa</)
})


test('wires multi-channel YouTube comments and Channel Hub into navigation', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')
  const commentsSource = await readFile(new URL('./YouTubeComments.jsx', import.meta.url), 'utf8')
  const channelManagerSource = await readFile(new URL('./ChannelManager.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /import YouTubeComments from ['"]\.\/YouTubeComments['"]/)
  assert.match(appSource, />Bình luận YouTube<\/li>/)
  assert.match(appSource, /activeView === ['"]comments['"]/)
  assert.match(appSource, /<YouTubeComments[\s\S]*onOpenVideo=\{viewSavedVideo\}/)
  assert.match(commentsSource, /Liên kết video đã đăng/)
  assert.match(commentsSource, /api\/youtube-comments\/draft/)
  assert.match(commentsSource, /api\/youtube-comments\/publish/)
  assert.match(appSource, /import ChannelManager from ['"]\.\/ChannelManager['"]/)
  assert.match(appSource, />Channel Hub<\/li>/)
  assert.match(appSource, /activeView === ['"]channels['"]/)
  assert.match(appSource, /<ChannelManager\s*\/>/)
  assert.match(channelManagerSource, /Channel Hub/)
  assert.match(channelManagerSource, /Kênh YouTube/)
  assert.match(channelManagerSource, /Kênh Facebook/)
  assert.match(channelManagerSource, /Kênh TikTok/)
  assert.match(channelManagerSource, /Trung tâm Profile GPM/)
})


test('wires the provider-neutral TTS catalog into navigation', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')
  const ttsSource = await readFile(new URL('./TTSSettings.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /import TTSSettings from ['"]\.\/TTSSettings['"]/)
  assert.match(appSource, />Giọng đọc &amp; TTS<\/li>/)
  assert.match(appSource, /activeView === ['"]tts['"]/)
  assert.match(appSource, /<TTSSettings\s*\/>/)
  assert.match(ttsSource, /\/providers\/omnivoice\/sync/)
  assert.match(ttsSource, /\/providers\/omnivoice\/clone/)
  assert.match(ttsSource, /capabilities\?\.billable/)
  assert.match(ttsSource, /Đã lưu trữ/)
  assert.match(ttsSource, /Nghe thử OmniVoice/)
  assert.match(ttsSource, /OMNIVOICE_PREVIEW_MAX_CHARACTERS = 200/)
  assert.match(ttsSource, /api\('\/previews'/)
  assert.match(ttsSource, /\/previews\/\$\{preview\.id\}\/cancel/)
  assert.match(ttsSource, /<audio[\s\S]*preview\.audio_url/)
  assert.match(ttsSource, /localStorage\.getItem\(OMNIVOICE_PREVIEW_STORAGE_KEY\)/)
})


test('shows automatic script review and starts audio without manual approval', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')
  const reviewSource = await readFile(new URL('./AudioReviewPanel.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /api\/videos\/\$\{currentVideoId\}\/audio-review`/)
  assert.match(appSource, /\$\{currentVideoId\}\/generate-audio`/)
  assert.doesNotMatch(appSource, /Duyệt kịch bản hiện tại.*Genmax có thể trừ credit/)
  assert.match(appSource, /<AudioReviewPanel/)
  assert.match(reviewSource, /không phải ngưỡng bắt buộc/)
  assert.match(reviewSource, /Đã tự động duyệt/)
  assert.match(reviewSource, /bạn không cần xác nhận/)
})


test('deleting a dashboard video refreshes every video view and clears stale detail state', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /const clearCurrentVideo = \(\) =>/)
  assert.match(appSource, /if \(!response\.ok \|\| data\.success === false\)/)
  assert.match(appSource, /if \(currentVideoId === id\) clearCurrentVideo\(\)/)
  assert.match(appSource, /setQueueRefreshKey\(key => key \+ 1\)/)
  assert.match(appSource, /savedVideos\.items\.length === 1 && currentPage > 1/)
  assert.match(appSource, /data\.warnings\?\.length/)
  assert.match(appSource, /<JobCenter[\s\S]*refreshKey=\{queueRefreshKey\}/)
})

test('expired ChatGPT sessions show automatic login recovery state', async () => {
  const jobCenterSource = await readFile(new URL('./JobCenter.jsx', import.meta.url), 'utf8')
  const settingsSource = await readFile(new URL('./Settings.jsx', import.meta.url), 'utf8')
  const autoLoginSource = await readFile(new URL('./AutoLogin.jsx', import.meta.url), 'utf8')

  assert.match(jobCenterSource, /job\.automatic_login === 'pending'/)
  assert.match(jobCenterSource, /Đang Auto Login/)
  assert.match(jobCenterSource, /sẽ tự tiếp tục đúng job này/)
  assert.match(settingsSource, /tự chạy Auto Login một lần/)
  assert.match(autoLoginSource, /Job sẽ tự chạy Auto Login một lần/)
})

test('wires Cross-Poster into navigation and view rendering', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')
  const crossPosterSource = await readFile(new URL('./CrossPoster.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /import CrossPoster from ['"]\.\/CrossPoster['"]/)
  assert.match(appSource, />Cross-Poster<\/li>/)
  assert.match(appSource, /activeView === ['"]crossposter['"][\s\S]*<CrossPoster/)
  assert.match(crossPosterSource, /Cross-Poster: Multi-Platform Video Syndication/)
  assert.match(crossPosterSource, /Quét Kênh YouTube/)
  assert.match(crossPosterSource, /Tính Lại Lịch Đăng/)
  assert.match(crossPosterSource, /api\/fb-crossposter\/settings/)
  assert.match(crossPosterSource, /api\/fb-crossposter\/queue/)
})

