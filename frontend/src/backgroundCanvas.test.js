import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

test('wires BackgroundCanvas and modes switcher into App', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')
  const canvasSource = await readFile(new URL('./BackgroundCanvas.jsx', import.meta.url), 'utf8')
  const stylesSource = await readFile(new URL('./BackgroundCanvas.css', import.meta.url), 'utf8')

  assert.match(appSource, /import BackgroundCanvas,\s*\{\s*BACKGROUND_MODES\s*\}\s*from ['"]\.\/BackgroundCanvas['"]/)
  assert.match(appSource, /<BackgroundCanvas\s+mode=\{bgMode\}\s*\/>/)
  assert.match(appSource, /localStorage\.getItem\(['"]nexus_bg_mode['"]\)/)
  assert.match(appSource, /className="bg-switcher-container"/)

  assert.match(canvasSource, /export const BACKGROUND_MODES = \[/)
  assert.match(canvasSource, /id:\s*['"]particles['"]/)
  assert.match(canvasSource, /id:\s*['"]aurora['"]/)
  assert.match(canvasSource, /id:\s*['"]grid['"]/)
  assert.match(canvasSource, /id:\s*['"]off['"]/)

  assert.match(canvasSource, /document\.addEventListener\(['"]visibilitychange['"]/)
  assert.match(canvasSource, /requestAnimationFrame\(render\)/)

  assert.match(stylesSource, /\.nexus-background-layer/)
  assert.match(stylesSource, /\.bg-switcher-container/)
  assert.match(stylesSource, /\.bg-mode-btn\.active/)
})
