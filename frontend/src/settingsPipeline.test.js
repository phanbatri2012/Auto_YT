import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const settingsSource = readFileSync(new URL('./Settings.jsx', import.meta.url), 'utf8');

test('Settings exposes a separately saved pipeline for every prompt version', () => {
  assert.match(settingsSource, /\/pipeline`/);
  assert.match(settingsSource, /thumbnail_with_text/);
  assert.match(settingsSource, /thumbnail_without_text/);
  assert.match(settingsSource, /Tự động tạo audio/);
});

test('duplicating a prompt version also copies its pipeline', () => {
  assert.match(settingsSource, /pipeline:\s*\{[\s\S]*newData\.versions\[activeVersion\]\.pipeline/);
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
  assert.match(settingsSource, /job sẽ tạm dừng thay vì tự mở/);
});
