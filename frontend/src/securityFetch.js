const API_BASE_URL = 'http://127.0.0.1:8080'
const API_ORIGINS = new Set([
  'http://127.0.0.1:8080',
  'http://localhost:8080',
])
const SESSION_ENDPOINT = `${API_BASE_URL}/api/security/session`
const CSRF_HEADER = 'X-AutoYT-CSRF'

let csrfToken = ''
let sessionPromise = null
let installed = false

function isLocalApiRequest(input) {
  const rawUrl = input instanceof Request ? input.url : String(input)
  const url = new URL(rawUrl, window.location.href)
  return API_ORIGINS.has(url.origin) && url.pathname.startsWith('/api/')
}

async function createLocalSession(nativeFetch) {
  const response = await nativeFetch(SESSION_ENDPOINT, {
    method: 'GET',
    credentials: 'include',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`Không thể tạo phiên API cục bộ (${response.status}).`)
  }
  const payload = await response.json()
  if (!payload?.csrf_token) {
    throw new Error('Backend không trả về mã bảo vệ phiên API.')
  }
  csrfToken = payload.csrf_token
}

async function ensureLocalSession(nativeFetch, force = false) {
  if (force) {
    csrfToken = ''
    sessionPromise = null
  }
  if (csrfToken) return
  if (!sessionPromise) {
    sessionPromise = createLocalSession(nativeFetch).finally(() => {
      sessionPromise = null
    })
  }
  await sessionPromise
}

function buildProtectedRequest(input, init) {
  let url = input
  let method = init?.method || 'GET'
  let body = init?.body
  const baseHeaders = init?.headers || (input instanceof Request ? input.headers : undefined)
  const headers = new Headers(baseHeaders)

  if (csrfToken) {
    headers.set(CSRF_HEADER, csrfToken)
  }

  if (input instanceof Request) {
    url = input.url
    method = input.method || method
  }

  const reqInit = {
    method,
    headers,
    credentials: 'include',
  }

  if (body !== undefined && body !== null && method !== 'GET' && method !== 'HEAD') {
    reqInit.body = body
  }

  return new Request(url, reqInit)
}

export function installSecurityFetch() {
  if (installed) return
  installed = true
  const nativeFetch = globalThis.fetch.bind(globalThis)

  globalThis.fetch = async (input, init) => {
    if (!isLocalApiRequest(input)) {
      return nativeFetch(input, init)
    }

    await ensureLocalSession(nativeFetch)
    let response
    try {
      response = await nativeFetch(buildProtectedRequest(input, init))
    } catch (err) {
      // Network error or connection reset during restart, retry once after renewing session
      await ensureLocalSession(nativeFetch, true)
      return nativeFetch(buildProtectedRequest(input, init))
    }

    if (response.status === 401 || response.status === 403) {
      await ensureLocalSession(nativeFetch, true)
      response = await nativeFetch(buildProtectedRequest(input, init))
    }
    return response
  }
}
