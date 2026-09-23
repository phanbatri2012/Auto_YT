import assert from 'node:assert/strict'
import test from 'node:test'
import {
  ROUTE_REGISTRY,
  parseRoute,
  buildPath,
  normalizeSlug,
  parseQueryString,
  stringifyQueryParams
} from './router.js'

test('registers all 11 primary modules in ROUTE_REGISTRY', () => {
  const expectedKeys = [
    'dashboard', 'jobs', 'comments', 'channels', 'crossposter',
    'fetcher', 'downloader', 'autologin', 'flowlogin', 'tts', 'settings'
  ]
  for (const key of expectedKeys) {
    assert.ok(ROUTE_REGISTRY[key], `Missing registry entry for ${key}`)
    assert.equal(ROUTE_REGISTRY[key].id, key)
  }
})

test('normalizes slugs correctly for case-insensitive matching', () => {
  assert.equal(normalizeSlug('/Cross-Poster/'), 'cross-poster')
  assert.equal(normalizeSlug('///jobs///'), 'jobs')
  assert.equal(normalizeSlug('  CHANNEL-HUB  '), 'channel-hub')
  assert.equal(normalizeSlug(''), '')
})

test('parses query strings and formats them back reliably', () => {
  const query = parseQueryString('?status=running&filter=all&page=2')
  assert.deepEqual(query, { status: 'running', filter: 'all', page: '2' })

  const emptyQuery = parseQueryString('')
  assert.deepEqual(emptyQuery, {})

  const formatted = stringifyQueryParams({ status: 'failed', limit: 10, empty: '' })
  assert.equal(formatted, '?status=failed&limit=10')
})

test('parses primary routes across all 11 menus correctly', () => {
  const expectedMappings = [
    { url: '/', viewId: 'dashboard' },
    { url: '/dashboard', viewId: 'dashboard' },
    { url: '/jobs', viewId: 'jobs' },
    { url: '/job-center', viewId: 'jobs' },
    { url: '/comments', viewId: 'comments' },
    { url: '/youtube-comments', viewId: 'comments' },
    { url: '/channels', viewId: 'channels' },
    { url: '/channel-hub', viewId: 'channels' },
    { url: '/Cross-Poster', viewId: 'crossposter' },
    { url: '/cross-poster', viewId: 'crossposter' },
    { url: '/crossposter', viewId: 'crossposter' },
    { url: '/fetcher', viewId: 'fetcher' },
    { url: '/video-fetcher', viewId: 'fetcher' },
    { url: '/downloader', viewId: 'downloader' },
    { url: '/youtube-downloader', viewId: 'downloader' },
    { url: '/autologin', viewId: 'autologin' },
    { url: '/auto-login', viewId: 'autologin' },
    { url: '/flowlogin', viewId: 'flowlogin' },
    { url: '/google-flow', viewId: 'flowlogin' },
    { url: '/tts', viewId: 'tts' },
    { url: '/settings', viewId: 'settings' }
  ]

  for (const item of expectedMappings) {
    const parsed = parseRoute(item.url)
    assert.equal(parsed.viewId, item.viewId, `Expected ${item.url} to resolve to ${item.viewId}`)
  }
})

test('parses deep sub-paths for Cross-Poster (/Cross-Poster/abc/xyz)', () => {
  const parsed = parseRoute('/Cross-Poster/abc/xyz')
  assert.equal(parsed.viewId, 'crossposter')
  assert.equal(parsed.subPath, 'abc/xyz')
  assert.deepEqual(parsed.segments, ['abc', 'xyz'])
  assert.equal(parsed.canonicalPath, '/Cross-Poster/abc/xyz')

  const parsedLower = parseRoute('/cross-poster/campaigns/meta-sync')
  assert.equal(parsedLower.viewId, 'crossposter')
  assert.equal(parsedLower.subPath, 'campaigns/meta-sync')
  assert.deepEqual(parsedLower.segments, ['campaigns', 'meta-sync'])
})

test('parses sub-routes for Channel Hub tabs (/channels/facebook, /channels/gpm)', () => {
  const youtubeRoute = parseRoute('/channels/youtube')
  assert.equal(youtubeRoute.viewId, 'channels')
  assert.equal(youtubeRoute.subPath, 'youtube')
  assert.deepEqual(youtubeRoute.segments, ['youtube'])

  const fbRoute = parseRoute('/channels/facebook')
  assert.equal(fbRoute.viewId, 'channels')
  assert.equal(fbRoute.subPath, 'facebook')
  assert.deepEqual(fbRoute.segments, ['facebook'])

  const gpmRoute = parseRoute('/channels/gpm')
  assert.equal(gpmRoute.viewId, 'channels')
  assert.equal(gpmRoute.subPath, 'gpm')
  assert.deepEqual(gpmRoute.segments, ['gpm'])
})

test('builds canonical URLs with sub-paths and query parameters', () => {
  assert.equal(buildPath('crossposter'), '/Cross-Poster')
  assert.equal(buildPath('crossposter', 'abc/xyz'), '/Cross-Poster/abc/xyz')
  assert.equal(
    buildPath('jobs', '', { status: 'failed', search: 'reels' }),
    '/jobs?status=failed&search=reels'
  )
  assert.equal(buildPath('channels', 'youtube'), '/channels/youtube')
  assert.equal(buildPath('dashboard'), '/dashboard')
})
