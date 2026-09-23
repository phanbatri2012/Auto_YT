function normalizedText(value) {
  return String(value || '').trim().toLocaleLowerCase('vi-VN')
}

export function normalizeYoutubeChannel(channel) {
  return {
    ...channel,
    channel_title: channel?.channel_title || channel?.title || channel?.name || ''
  }
}

export function resolveYoutubeChannelSelection(channels, settings) {
  const source = normalizedText(settings?.source_channel_id)
  const sourceTitle = normalizedText(settings?.source_channel_title)
  const profileId = String(settings?.source_gpm_profile_id || '').trim()

  const exactChannel = channels.find(channel => {
    const channelId = normalizedText(channel?.channel_id)
    return channelId && (
      source === channelId ||
      source.includes(`/channel/${channelId}`)
    )
  })
  if (exactChannel) return String(exactChannel.channel_id || exactChannel.id || '')

  const exactTitle = channels.find(channel => (
    sourceTitle && normalizedText(channel?.channel_title || channel?.title || channel?.name) === sourceTitle
  ))
  if (exactTitle) return String(exactTitle.channel_id || exactTitle.id || '')

  const profileMatches = channels.filter(channel => (
    profileId && String(channel?.gpm_profile_id || '').trim() === profileId
  ))
  if (profileMatches.length === 1) {
    return String(profileMatches[0].channel_id || profileMatches[0].id || '')
  }
  return ''
}

export function resolveFacebookPageSelection(pages, settings, selectedPageId) {
  const pageId = String(settings?.target_fb_page_id || selectedPageId || '').trim()
  return pages.some(page => String(page?.page_id || '').trim() === pageId) ? pageId : ''
}
