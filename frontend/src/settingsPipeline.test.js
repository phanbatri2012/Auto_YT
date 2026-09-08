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
