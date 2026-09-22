import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const settingsSource = readFileSync(new URL('./Settings.jsx', import.meta.url), 'utf8');
const appSource = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8');
const jobCenterSource = readFileSync(new URL('./JobCenter.jsx', import.meta.url), 'utf8');

test('Settings exposes a separately saved pipeline for every prompt version', () => {
  assert.match(settingsSource, /\/pipeline`/);
  assert.match(settingsSource, /thumbnail_with_text/);
  assert.match(settingsSource, /thumbnail_without_text/);
  assert.match(settingsSource, /Tự động tạo audio/);
  assert.match(settingsSource, /video_render/);
  assert.match(settingsSource, /youtube_upload/);
  assert.match(settingsSource, /youtube_schedule/);
});

test('duplicating a prompt version also copies its pipeline', () => {
  assert.match(settingsSource, /pipeline:\s*\{[\s\S]*newData\.versions\[activeVersion\]\.pipeline/);
  assert.match(settingsSource, /image_generation_settings:\s*\{[\s\S]*newData\.versions\[activeVersion\]\.image_generation_settings/);
  assert.match(settingsSource, /publishing_settings:\s*\{[\s\S]*newData\.versions\[activeVersion\]\.publishing_settings/);
});

test('pipeline dependencies and downstream cascade are applied before saving', () => {
  assert.match(settingsSource, /resolvePipelineDependencies/);
  assert.match(settingsSource, /pipelineDependents/);
  assert.match(settingsSource, /Tắt bước này cũng sẽ tắt/);
  assert.match(settingsSource, /Đã tự bật/);
});

test('Settings configures scene image generation and YouTube publishing only for enabled stages', () => {
  assert.match(settingsSource, /currentPipeline\.video_render &&/);
  assert.match(settingsSource, /currentPipeline\.youtube_upload &&/);
  assert.match(settingsSource, /\/image-generation`/);
  assert.match(settingsSource, /\/publishing`/);
  assert.match(settingsSource, /Thumbnail dùng để upload/);
  assert.match(settingsSource, /scene_duration_target_seconds/);
  assert.match(settingsSource, /style_prompt/);
  assert.match(settingsSource, /Dành cho trẻ em/);
  assert.match(settingsSource, /Luôn khai báo nội dung tổng hợp bằng AI/);
});

test('Settings saves a stable default YouTube channel for every prompt version', () => {
  assert.match(settingsSource, /\/default-youtube-channel`/);
  assert.match(settingsSource, /default_youtube_channel_id/);
  assert.match(settingsSource, /Kênh YouTube mặc định của bộ prompt/);
});

test('duplicating a prompt version also copies its default YouTube channel', () => {
  assert.match(
    settingsSource,
    /default_youtube_channel_id:\s*[\s\S]*newData\.versions\[activeVersion\]\.default_youtube_channel_id/
  );
});

test('Settings exposes hidden ChatGPT jobs and a strict game mode', () => {
  assert.match(settingsSource, /\/api\/browser-automation/);
  assert.match(settingsSource, /\/api\/chatgpt-browser-service/);
  assert.match(settingsSource, /worker_headless/);
  assert.match(settingsSource, /Chế độ chơi game/);
  assert.match(settingsSource, /Khởi động trình duyệt nền/);
  assert.match(settingsSource, /Hiện trình duyệt/);
  assert.match(settingsSource, /Ẩn trình duyệt/);
  assert.match(settingsSource, /window_visible/);
  assert.match(settingsSource, /tự chạy Auto Login một lần/);
  assert.match(settingsSource, /CAPTCHA, MFA thiếu mã/);
});

test('Dashboard and Job Center expose render and YouTube publish artifacts', () => {
  assert.match(appSource, /videoProductionStatus/);
  assert.match(appSource, /final_artifact_id/);
  assert.match(appSource, /YouTube Studio/);
  assert.match(jobCenterSource, /video_render/);
  assert.match(jobCenterSource, /youtube_publish/);
  assert.match(jobCenterSource, /artifact_download_url/);
  assert.match(jobCenterSource, /youtube_studio_url/);
});

test('App detail panel exposes MP4 render button, status, and download controls', () => {
  assert.match(appSource, /\/render-video/);
  assert.match(appSource, /\/render-status/);
  assert.match(appSource, /\/download-mp4/);
  assert.match(appSource, /\/cancel-render/);
  assert.match(appSource, /Dựng video MP4/);
  assert.match(appSource, /Tải Video MP4/);
});

