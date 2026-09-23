/**
 * Auto_YT Universal Router Engine
 * Zero-dependency, modern HTML5 History API router with deep sub-path & parameter support.
 * Designed for Nexus Studio Engine with React 19 compatibility.
 */

import { createElement, useEffect, useState, useCallback, useMemo } from 'react'

export const NAVIGATION_EVENT = 'autoyt-navigation'

/**
 * Registry of all primary routes, canonical URLs, aliases, and display titles.
 */
export const ROUTE_REGISTRY = {
  dashboard: {
    id: 'dashboard',
    canonicalPath: '/dashboard',
    aliases: ['/', '/dashboard', '/trang-chu', '/library', '/video-library'],
    title: 'Dashboard - Video Library'
  },
  jobs: {
    id: 'jobs',
    canonicalPath: '/jobs',
    aliases: ['/jobs', '/job-center', '/trung-tam-job', '/queue'],
    title: 'Trung tâm Job'
  },
  comments: {
    id: 'comments',
    canonicalPath: '/comments',
    aliases: ['/comments', '/youtube-comments', '/binh-luan'],
    title: 'Bình luận YouTube'
  },
  channels: {
    id: 'channels',
    canonicalPath: '/channels',
    aliases: ['/channels', '/channel-hub', '/kenh', '/channel-manager'],
    title: 'Channel Hub'
  },
  crossposter: {
    id: 'crossposter',
    canonicalPath: '/Cross-Poster',
    aliases: ['/cross-poster', '/crossposter', '/cross_poster', '/syndication'],
    title: 'Cross-Poster'
  },
  fetcher: {
    id: 'fetcher',
    canonicalPath: '/fetcher',
    aliases: ['/fetcher', '/video-fetcher', '/tao-video', '/script-editor'],
    title: 'Video Fetcher'
  },
  downloader: {
    id: 'downloader',
    canonicalPath: '/downloader',
    aliases: ['/downloader', '/youtube-downloader', '/tai-video'],
    title: 'YouTube Downloader'
  },
  autologin: {
    id: 'autologin',
    canonicalPath: '/autologin',
    aliases: ['/autologin', '/auto-login', '/chatgpt-login'],
    title: 'Auto Login ChatGPT'
  },
  flowlogin: {
    id: 'flowlogin',
    canonicalPath: '/flowlogin',
    aliases: ['/flowlogin', '/google-flow', '/flow-login'],
    title: 'Google Flow'
  },
  tts: {
    id: 'tts',
    canonicalPath: '/tts',
    aliases: ['/tts', '/tts-settings', '/giong-doc-tts', '/omnivoice'],
    title: 'Giọng đọc & TTS'
  },
  settings: {
    id: 'settings',
    canonicalPath: '/settings',
    aliases: ['/settings', '/cai-dat', '/cau-hinh'],
    title: 'Settings'
  }
}

/**
 * Fast lookup map for normalized slug -> route ID
 */
const ALIAS_LOOKUP = new Map()
Object.values(ROUTE_REGISTRY).forEach(route => {
  route.aliases.forEach(alias => {
    const normalized = normalizeSlug(alias)
    ALIAS_LOOKUP.set(normalized, route.id)
  })
})

/**
 * Normalizes a slug or path string for matching (lowercase, no leading/trailing slashes).
 */
export function normalizeSlug(slug) {
  if (!slug) return ''
  return String(slug)
    .trim()
    .toLowerCase()
    .replace(/^\/+|\/+$/g, '')
}

/**
 * Parses a query string into a plain key-value object.
 */
export function parseQueryString(search) {
  if (!search) return {}
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
  const result = {}
  for (const [key, value] of params.entries()) {
    result[key] = value
  }
  return result
}

/**
 * Serializes a plain object into a URL search query string.
 */
export function stringifyQueryParams(queryObj) {
  if (!queryObj || typeof queryObj !== 'object') return ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(queryObj)) {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value))
    }
  }
  const queryString = params.toString()
  return queryString ? `?${queryString}` : ''
}

/**
 * Parses full pathname and optional search into structured route data.
 * Supports deep sub-paths (e.g. /Cross-Poster/abc/xyz) and parameters.
 */
export function parseRoute(rawPathname = '/', rawSearch = '') {
  let pathname = String(rawPathname || '/').trim()
  if (!pathname.startsWith('/')) pathname = '/' + pathname

  const search = String(rawSearch || '')
  const query = parseQueryString(search)

  // Clean and split path segments
  const cleanPath = pathname.replace(/^\/+|\/+$/g, '')
  const allSegments = cleanPath ? cleanPath.split('/') : []

  if (allSegments.length === 0) {
    return {
      viewId: 'dashboard',
      canonicalPath: '/',
      subPath: '',
      segments: [],
      query,
      pathname: '/',
      rawPathname
    }
  }

  const firstSegment = allSegments[0]
  const normalizedFirst = normalizeSlug(firstSegment)
  const matchedViewId = ALIAS_LOOKUP.get(normalizedFirst)

  if (matchedViewId) {
    const routeConfig = ROUTE_REGISTRY[matchedViewId]
    const subSegments = allSegments.slice(1)
    const subPath = subSegments.join('/')
    const canonicalPath = routeConfig.canonicalPath + (subPath ? `/${subPath}` : '')

    return {
      viewId: matchedViewId,
      canonicalPath,
      subPath,
      segments: subSegments,
      query,
      pathname,
      rawPathname
    }
  }

  // Fallback: If not matched directly, check if it's a direct entity route (e.g. /video/123)
  if (normalizedFirst === 'video' && allSegments.length > 1) {
    return {
      viewId: 'dashboard',
      canonicalPath: `/dashboard/${cleanPath}`,
      subPath: cleanPath,
      segments: allSegments,
      query,
      pathname,
      rawPathname
    }
  }

  // Unknown route fallback: default to dashboard, preserve sub-path
  return {
    viewId: 'dashboard',
    canonicalPath: '/dashboard',
    subPath: cleanPath,
    segments: allSegments,
    query,
    pathname,
    rawPathname
  }
}

/**
 * Builds a canonical URL path for a given view ID or path and optional sub-path / query.
 */
export function buildPath(target, subPath = '', queryObj = null) {
  let basePath = ''
  if (ROUTE_REGISTRY[target]) {
    basePath = ROUTE_REGISTRY[target].canonicalPath
  } else if (typeof target === 'string') {
    if (target.startsWith('/')) {
      basePath = target
    } else {
      const normalized = normalizeSlug(target)
      const mappedId = ALIAS_LOOKUP.get(normalized)
      basePath = mappedId ? ROUTE_REGISTRY[mappedId].canonicalPath : `/${target}`
    }
  } else {
    basePath = '/dashboard'
  }

  let fullPath = basePath
  const cleanSub = String(subPath || '').replace(/^\/+|\/+$/g, '')
  if (cleanSub) {
    if (fullPath === '/') {
      fullPath = `/${cleanSub}`
    } else {
      fullPath = `${fullPath.replace(/\/+$/, '')}/${cleanSub}`
    }
  }

  const queryString = stringifyQueryParams(queryObj)
  return `${fullPath}${queryString}`
}

/**
 * Dispatches navigation event to notify listening React components.
 */
function emitNavigationEvent(routeData) {
  if (typeof window === 'undefined') return
  const event = new CustomEvent(NAVIGATION_EVENT, { detail: routeData })
  window.dispatchEvent(event)
}

/**
 * Navigates to a new URL path or view ID using HTML5 History API.
 */
export function navigateTo(target, subPath = '', options = {}) {
  if (typeof window === 'undefined') return

  const { replace = false, query = null, state = null } = options
  const targetUrl = buildPath(target, subPath, query)
  const currentUrl = window.location.pathname + window.location.search

  if (targetUrl !== currentUrl) {
    if (replace) {
      window.history.replaceState(state, '', targetUrl)
    } else {
      window.history.pushState(state, '', targetUrl)
    }
  }

  const parsed = parseRoute(window.location.pathname, window.location.search)
  emitNavigationEvent(parsed)
}

/**
 * Replaces current URL in history without pushing a new stack entry.
 */
export function replaceTo(target, subPath = '', options = {}) {
  navigateTo(target, subPath, { ...options, replace: true })
}

/**
 * React Hook for application-wide routing, deep link parsing, and URL synchronization.
 */
export function useAppRouter() {
  const getInitialRoute = () => {
    if (typeof window === 'undefined') {
      return parseRoute('/', '')
    }
    return parseRoute(window.location.pathname, window.location.search)
  }

  const [route, setRoute] = useState(getInitialRoute)

  useEffect(() => {
    if (typeof window === 'undefined') return

    const handleLocationChange = () => {
      const parsed = parseRoute(window.location.pathname, window.location.search)
      setRoute(parsed)
    }

    const handleCustomNav = (event) => {
      if (event.detail) {
        setRoute(event.detail)
      } else {
        handleLocationChange()
      }
    }

    window.addEventListener('popstate', handleLocationChange)
    window.addEventListener(NAVIGATION_EVENT, handleCustomNav)

    return () => {
      window.removeEventListener('popstate', handleLocationChange)
      window.removeEventListener(NAVIGATION_EVENT, handleCustomNav)
    }
  }, [])

  const navigate = useCallback((target, subPath = '', options = {}) => {
    navigateTo(target, subPath, options)
  }, [])

  const replace = useCallback((target, subPath = '', options = {}) => {
    replaceTo(target, subPath, options)
  }, [])

  const setQuery = useCallback((queryUpdates, options = {}) => {
    if (typeof window === 'undefined') return
    const currentQuery = parseQueryString(window.location.search)
    const newQuery = { ...currentQuery, ...queryUpdates }
    const targetUrl = buildPath(route.viewId, route.subPath, newQuery)
    if (options.replace !== false) {
      window.history.replaceState(null, '', targetUrl)
    } else {
      window.history.pushState(null, '', targetUrl)
    }
    const parsed = parseRoute(window.location.pathname, window.location.search)
    setRoute(parsed)
    emitNavigationEvent(parsed)
  }, [route.viewId, route.subPath])

  return {
    activeView: route.viewId,
    subPath: route.subPath,
    segments: route.segments,
    query: route.query,
    pathname: route.pathname,
    canonicalPath: route.canonicalPath,
    rawRoute: route,
    navigate,
    replace,
    setQuery
  }
}

/**
 * React Hook for sub-routing inside child components (e.g. ChannelManager tabs).
 */
export function useSubRoute(baseViewId, defaultSubPath = '') {
  const router = useAppRouter()

  const currentSub = useMemo(() => {
    if (router.activeView !== baseViewId) return ''
    return router.subPath || defaultSubPath
  }, [router.activeView, router.subPath, baseViewId, defaultSubPath])

  const setSubRoute = useCallback((newSubPath, options = {}) => {
    router.navigate(baseViewId, newSubPath, { replace: true, ...options })
  }, [router, baseViewId])

  return [currentSub, setSubRoute, router.segments]
}

/**
 * Lightweight SPA Link component using React.createElement.
 */
export function Link({ to, subPath = '', query = null, children, className = '', onClick, replace = false, ...rest }) {
  const href = buildPath(to, subPath, query)

  const handleClick = (e) => {
    // Allow default behavior for modified clicks (Ctrl+Click, Cmd+Click, middle click)
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) {
      return
    }
    e.preventDefault()
    if (onClick) onClick(e)
    navigateTo(to, subPath, { replace, query })
  }

  return createElement('a', {
    href,
    className,
    onClick: handleClick,
    ...rest
  }, children)
}
