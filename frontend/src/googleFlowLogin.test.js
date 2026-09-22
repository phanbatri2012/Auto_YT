import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'


test('wires Google Flow into navigation', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /import GoogleFlowLogin from ['"]\.\/GoogleFlowLogin['"]/)
  assert.match(appSource, />Google Flow<\/li>/)
  assert.match(appSource, /activeView === ['"]flowlogin['"]/)
  assert.match(appSource, /<GoogleFlowLogin\s*\/>/)
})


test('keeps unsaved credentials while status polling runs', async () => {
  const source = await readFile(new URL('./GoogleFlowLogin.jsx', import.meta.url), 'utf8')

  assert.match(source, /fetchStatus\(true\)/)
  assert.match(source, /email: hydrateCredentials \? \(accData\.email \|\| ''\) : prev\.email/)
  assert.match(source, /password: hydrateCredentials \? '' : prev\.password/)
  assert.match(source, /totp_secret: hydrateCredentials \? '' : prev\.totp_secret/)
})


test('validates API responses and renders failures as errors', async () => {
  const source = await readFile(new URL('./GoogleFlowLogin.jsx', import.meta.url), 'utf8')
  const styles = await readFile(new URL('./AutoLogin.css', import.meta.url), 'utf8')

  assert.match(source, /if \(!response\.ok\)/)
  assert.match(source, /placeholder="Nhập email Google"/)
  assert.match(source, /className=\{`result \$\{resultType\}`\}/)
  assert.doesNotMatch(source, /disabled=\{!isConnected\}/)
  assert.match(styles, /\.result\.error\s*\{[\s\S]*color: #e74c3c/)
})
