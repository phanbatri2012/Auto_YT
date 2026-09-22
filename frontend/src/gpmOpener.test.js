import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const gpmOpenerSource = readFileSync(
  new URL('./gpmOpener.js', import.meta.url),
  'utf8'
)
const appSource = readFileSync(
  new URL('./App.jsx', import.meta.url),
  'utf8'
)
const jobCenterSource = readFileSync(
  new URL('./JobCenter.jsx', import.meta.url),
  'utf8'
)
const channelManagerSource = readFileSync(
  new URL('./ChannelManager.jsx', import.meta.url),
  'utf8'
)

test('gpmOpener utility exposes dedicated GPM channel navigation methods', () => {
  assert.match(gpmOpenerSource, /export async function openUrlInGpm/)
  assert.match(gpmOpenerSource, /export async function openVideoStudioInGpm/)
  assert.match(gpmOpenerSource, /export async function openVideoWatchInGpm/)
  assert.match(gpmOpenerSource, /export async function openChannelStudioInGpm/)
  assert.match(gpmOpenerSource, /api\/gpm\/profiles\/.*\/open-url/)
  assert.match(gpmOpenerSource, /api\/videos\/.*\/open-studio/)
  assert.match(gpmOpenerSource, /api\/videos\/.*\/open-watch/)
  assert.match(gpmOpenerSource, /api\/youtube-comments\/channels\/.*\/open-studio/)
})

test('App, JobCenter, and ChannelManager enforce zero host browser leaks for channel operations', () => {
  // App.jsx should use openVideoStudioInGpm and openVideoWatchInGpm
  assert.match(appSource, /openVideoStudioInGpm/)
  assert.match(appSource, /openVideoWatchInGpm/)
  assert.doesNotMatch(appSource, /window\.open\(`https:\/\/studio\.youtube\.com/)

  // JobCenter.jsx should use openVideoStudioInGpm and avoid window.open fallback
  assert.match(jobCenterSource, /openVideoStudioInGpm/)
  assert.doesNotMatch(jobCenterSource, /window\.open\(job\.youtube_studio_url/)

  // ChannelManager.jsx should route OAuth through GPM profile and block unisolated window.open
  assert.match(channelManagerSource, /api\/gpm\/profiles\/.*\/open-url/)
  assert.doesNotMatch(channelManagerSource, /window\.open\(data\.authorization_url/)
})
