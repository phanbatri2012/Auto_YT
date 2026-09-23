export function extractErrorMessage(data, fallback = 'Lỗi không xác định') {
  if (!data) return fallback
  if (typeof data === 'string') return data
  if (typeof data.detail === 'string') return data.detail
  if (Array.isArray(data.detail)) {
    return (
      data.detail
        .map(item => {
          if (typeof item === 'string') return item
          if (item && typeof item === 'object') {
            return item.msg || item.message || JSON.stringify(item)
          }
          return String(item)
        })
        .filter(Boolean)
        .join('; ') || fallback
    )
  }
  if (typeof data.detail === 'object' && data.detail !== null) {
    return data.detail.msg || data.detail.message || JSON.stringify(data.detail)
  }
  if (typeof data.error === 'string') return data.error
  if (typeof data.message === 'string') return data.message
  return fallback
}
