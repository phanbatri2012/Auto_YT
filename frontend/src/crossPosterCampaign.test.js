import test from 'node:test'
import assert from 'node:assert/strict'

import {
  normalizeYoutubeChannel,
  resolveFacebookPageSelection,
  resolveYoutubeChannelSelection
} from './crossPosterCampaign.js'

test('normalizes the database title field for Cross-Poster selectors', () => {
  const channel = normalizeYoutubeChannel({ channel_id: 'UC-1', title: 'Kênh Một' })
  assert.equal(channel.channel_title, 'Kênh Một')
})

test('resolves the selected YouTube channel by stable identity priority', () => {
  const channels = [
    normalizeYoutubeChannel({ channel_id: 'UC-1', title: 'Kênh Một', gpm_profile_id: 'profile-1' }),
    normalizeYoutubeChannel({ channel_id: 'UC-2', title: 'Kênh Hai', gpm_profile_id: 'profile-2' })
  ]

  assert.equal(resolveYoutubeChannelSelection(channels, {
    source_channel_id: 'https://www.youtube.com/channel/UC-2',
    source_channel_title: 'Kênh Một',
    source_gpm_profile_id: 'profile-1'
  }), 'UC-2')
  assert.equal(resolveYoutubeChannelSelection(channels, {
    source_channel_title: 'Kênh Một',
    source_gpm_profile_id: 'profile-2'
  }), 'UC-1')
  assert.equal(resolveYoutubeChannelSelection(channels, {
    source_channel_id: 'https://www.youtube.com/@KenhHai',
    source_gpm_profile_id: 'profile-2'
  }), 'UC-2')
})

test('does not guess when a GPM profile belongs to multiple channels', () => {
  const channels = [
    { channel_id: 'UC-1', gpm_profile_id: 'shared' },
    { channel_id: 'UC-2', gpm_profile_id: 'shared' }
  ]
  assert.equal(resolveYoutubeChannelSelection(channels, {
    source_gpm_profile_id: 'shared'
  }), '')
})

test('selects only a Facebook page available in Channel Hub', () => {
  const pages = [{ page_id: 'page-1' }, { page_id: 'page-2' }]
  assert.equal(resolveFacebookPageSelection(pages, {
    target_fb_page_id: 'page-2'
  }, 'page-1'), 'page-2')
  assert.equal(resolveFacebookPageSelection(pages, {}, 'missing'), '')
})
