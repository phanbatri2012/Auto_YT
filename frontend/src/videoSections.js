const MAIN_SCRIPT_TAGS = new Set(['INTRO', 'BODY', 'OUTRO'])
const IGNORED_TAGS = new Set(['IMAGE', 'AUDIO'])

function trimEmptyLines(lines) {
  let start = 0
  let end = lines.length

  while (start < end && !lines[start].trim()) start += 1
  while (end > start && !lines[end - 1].trim()) end -= 1

  return lines.slice(start, end)
}

function normalizeLabel(value) {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/đ/g, 'd')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

function getMetadataLabel(line) {
  const cleaned = line
    .trim()
    .replace(/^[-*\s]+/, '')
    .replace(/^\d+[.)]\s*/, '')
    .replace(/\*+/g, '')
  const separatorIndex = cleaned.indexOf(':')
  if (separatorIndex < 0) return null

  const rawLabel = cleaned.slice(0, separatorIndex).trim()
  if (!rawLabel || rawLabel.length > 40) return null

  const label = normalizeLabel(rawLabel)
  const value = cleaned.slice(separatorIndex + 1).trim()

  if (['tieu de', 'tieu de video', 'video title', 'title'].includes(label)) {
    return { type: 'title', value }
  }
  if (['slug', 'url slug', 'slug url'].includes(label)) {
    let cleanValue = value
    for (const prefix of ['url slug:', 'slug url:', 'slug:']) {
      if (cleanValue.toLowerCase().startsWith(prefix)) {
        cleanValue = cleanValue.slice(prefix.length).trim()
      }
    }
    return { type: 'slug', value: cleanValue }
  }
  if (['mo ta', 'mo ta video', 'description'].includes(label)) {
    return { type: 'description', value }
  }
  if (['hashtag', 'hashtags'].includes(label)) {
    return { type: 'hashtags', value }
  }
  if (['tag', 'tags', 'the tu khoa', 'the'].includes(label)) {
    return { type: 'tags', value }
  }
  if (['binh luan ghim', 'pinned comment'].includes(label)) {
    return { type: 'pinnedComment', value }
  }
  if ([
    'quiz', 'quiz tuong tac', 'cau hoi', 'cau hoi quiz',
    'cau hoi khan gia', 'trac nghiem', 'cau hoi trac nghiem',
    'cau hoi va dap an', 'quiz khan gia'
  ].includes(label)) {
    return { type: 'quiz', value }
  }

  return null
}

function moveQuestionToQuiz(source, quiz) {
  let questionIndex = -1
  for (let index = source.length - 1; index >= 0; index -= 1) {
    const line = source[index].trim()
    if (!line) continue
    const clean = normalizeLabel(line)
    if (
      line.endsWith('?') ||
      clean.startsWith('theo quy vi') ||
      clean.startsWith('theo cac ban') ||
      clean.startsWith('cau hoi')
    ) {
      questionIndex = index
      break
    }
  }

  if (questionIndex >= 0) {
    quiz.push(...trimEmptyLines(source.splice(questionIndex)))
  }
}

function parseMetadataContent(content) {
  const fields = {
    title: [],
    slug: [],
    description: [],
    hashtags: [],
    tags: [],
    quiz: [],
    pinnedComment: []
  }
  let currentField = null
  let hashtagsSeen = false

  content.split(/\r?\n/).forEach((line) => {
    const label = getMetadataLabel(line)
    if (label) {
      currentField = label.type
      if (label.value) fields[currentField].push(label.value)
      if (label.type === 'title' || label.type === 'slug') currentField = null
      return
    }

    const trimmed = line.trim()
    const cleanLower = normalizeLabel(trimmed)

    if (
      (cleanLower.startsWith('theo quy vi') || cleanLower.startsWith('theo cac ban') || cleanLower.startsWith('cau hoi')) &&
      currentField === 'pinnedComment'
    ) {
      currentField = 'quiz'
      fields.quiz.push(line)
      return
    }

    if (/^(?:A|B|C|D)[.)]\s+/i.test(trimmed) && currentField !== 'quiz') {
      const questionSource = fields.pinnedComment.length
        ? fields.pinnedComment
        : fields.description
      moveQuestionToQuiz(questionSource, fields.quiz)
      currentField = 'quiz'
    }

    if (/#[\p{L}\p{N}_-]+/u.test(trimmed) && currentField !== 'quiz') {
      fields.hashtags.push(line)
      hashtagsSeen = true
      currentField = null
      return
    }

    if (!currentField && trimmed) {
      currentField = hashtagsSeen ? 'pinnedComment' : 'description'
    }

    if (currentField) fields[currentField].push(line)
  })

  return Object.fromEntries(
    Object.entries(fields).map(([key, lines]) => {
      let text = trimEmptyLines(lines).join('\n').trim()
      if (key === 'slug') {
        text = text.replace(/^(?:url\s+)?slug:\s*/i, '').trim()
      }
      return [key, text]
    })
  )
}

function buildDescriptionSection(metadata, chaptersContent) {
  const parts = []
  if (metadata.description) parts.push(metadata.description)
  if (chaptersContent) parts.push(chaptersContent)
  if (metadata.hashtags) parts.push(metadata.hashtags)
  else if (metadata.tags && metadata.tags.includes('#')) parts.push(metadata.tags)

  if (!parts.length) return null
  return {
    title: 'MÔ TẢ, TAG & CHAPTERS',
    content: parts.join('\n\n')
  }
}

export function parseVideoSections(text) {
  if (!text) return []

  const parts = text.split(/###\s*\[([^\]]+)\]/g)
  const mainScriptParts = []
  const otherSections = []
  let metadata = {
    title: '',
    slug: '',
    description: '',
    hashtags: '',
    tags: '',
    quiz: '',
    pinnedComment: ''
  }
  let chaptersContent = ''

  for (let index = 1; index < parts.length; index += 2) {
    const rawTag = parts[index].trim()
    const tag = rawTag.toUpperCase()
    const content = parts[index + 1]?.trim() || ''

    if (MAIN_SCRIPT_TAGS.has(tag)) {
      if (content) mainScriptParts.push(content)
    } else if (tag === 'METADATA & QUIZ') {
      const parsed = parseMetadataContent(content)
      metadata = { ...metadata, ...parsed }
    } else if (tag === 'TIÊU ĐỀ' || tag === 'TIEU DE' || tag === 'TITLE') {
      metadata.title = content.replace(/^[-*\s]+(?:tiêu\s+đề(?:\s+video)?|title):\s*/i, '').trim()
    } else if (tag === 'SLUG' || tag === 'URL SLUG') {
      metadata.slug = content.replace(/^[-*\s]+(?:url\s+)?slug:\s*/i, '').trim()
    } else if (tag === 'MÔ TẢ' || tag === 'MO TA' || tag === 'DESCRIPTION') {
      metadata.description = content.replace(/^[-*\s]+(?:mô\s+tả(?:\s+video)?|description):\s*/i, '').trim()
    } else if (tag === 'HASHTAGS' || tag === 'HASHTAG') {
      metadata.hashtags = content.replace(/^[-*\s]+(?:hashtags?):\s*/i, '').trim()
    } else if (tag === 'TAGS' || tag === 'TAG' || tag === 'THẺ TỪ KHÓA' || tag === 'THE TU KHOA') {
      metadata.tags = content.replace(/^[-*\s]+(?:tags?|thẻ(?:\s+từ\s+khóa)?):\s*/i, '').trim()
    } else if (tag === 'BÌNH LUẬN GHIM' || tag === 'BINH LUAN GHIM' || tag === 'PINNED COMMENT') {
      metadata.pinnedComment = content.replace(/^[-*\s]+(?:bình\s+luận\s+ghim|pinned\s+comment):\s*/i, '').trim()
    } else if (tag === 'QUIZ' || tag === 'QUIZ TƯƠNG TÁC' || tag === 'QUIZ TUONG TAC') {
      metadata.quiz = content
    } else if (tag === 'CHAPTERS' || tag === 'PHÂN ĐOẠN' || tag === 'PHAN DOAN') {
      chaptersContent = content
    } else if (!IGNORED_TAGS.has(tag)) {
      otherSections.push({ title: rawTag, content })
    }
  }

  const sections = []
  if (mainScriptParts.length) {
    sections.push({
      title: 'NỘI DUNG KỊCH BẢN',
      content: mainScriptParts.join('\n\n')
    })
  }

  if (metadata.title) {
    sections.push({ title: 'TIÊU ĐỀ VIDEO', content: metadata.title })
  }
  if (metadata.slug) {
    sections.push({ title: 'URL SLUG', content: metadata.slug })
  }

  const descriptionSection = buildDescriptionSection(metadata, chaptersContent)
  if (descriptionSection) sections.push(descriptionSection)

  if (metadata.tags && !metadata.tags.includes('#')) {
    sections.push({ title: 'THẺ TỪ KHÓA (TAGS)', content: metadata.tags })
  }
  if (metadata.quiz) {
    sections.push({ title: 'QUIZ', content: metadata.quiz })
  }
  if (metadata.pinnedComment) {
    sections.push({ title: 'BÌNH LUẬN GHIM', content: metadata.pinnedComment })
  }

  sections.push(...otherSections)

  if (!sections.length && text.trim()) {
    sections.push({ title: 'KẾT QUẢ TRẢ VỀ', content: text.trim() })
  }

  return sections
}
