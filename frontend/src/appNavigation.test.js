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
  assert.match(appSource, /<JobCenter onOpenVideo=\{viewSavedVideo\}/)
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
  assert.match(videoQueueSource, /'queued', 'running', 'retry_wait', 'paused'/)
  assert.match(videoQueueSource, /const hasPollableJobs = useMemo/)
  assert.match(videoQueueSource, /if \(!hasPollableJobs\) return undefined/)
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
