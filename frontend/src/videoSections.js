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
  if (['slug', 'url slug'].includes(label)) {
    return { type: 'slug', value }
  }
  if (['mo ta', 'mo ta video', 'description'].includes(label)) {
    return { type: 'description', value }
  }
  if (['tag', 'tags', 'hashtag', 'hashtags'].includes(label)) {
    return { type: 'tags', value }
  }
  if (['binh luan ghim', 'pinned comment'].includes(label)) {
    return { type: 'pinnedComment', value }
  }
  if (['quiz', 'quiz tuong tac', 'cau hoi', 'cau hoi quiz'].includes(label)) {
    return { type: 'quiz', value }
  }

  return null
}

function moveQuestionToQuiz(source, quiz) {
  let questionIndex = -1
  for (let index = source.length - 1; index >= 0; index -= 1) {
    const line = source[index].trim()
    if (!line) continue
    if (line.endsWith('?')) {
      questionIndex = index
    }
    break
  }

  if (questionIndex < 0) return
  quiz.push(...trimEmptyLines(source.splice(questionIndex)))
}

function parseMetadataContent(content) {
  const fields = {
    title: [],
    slug: [],
    description: [],
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
    if (/^(?:A|B|C|D)[.)]\s+/i.test(trimmed) && currentField !== 'quiz') {
      const questionSource = fields.pinnedComment.length
        ? fields.pinnedComment
        : fields.description
      moveQuestionToQuiz(questionSource, fields.quiz)
      currentField = 'quiz'
    }

    if (/#[\p{L}\p{N}_-]+/u.test(trimmed) && currentField !== 'quiz') {
      fields.tags.push(line)
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
    Object.entries(fields).map(([key, lines]) => [key, trimEmptyLines(lines).join('\n').trim()])
  )
}

function buildDescriptionSection(metadata, chaptersContent) {
  const parts = []
  if (metadata.description) parts.push(metadata.description)
  if (metadata.tags) parts.push(metadata.tags)
  if (chaptersContent) parts.push(chaptersContent)

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
  let metadata = null
  let chaptersContent = ''

  for (let index = 1; index < parts.length; index += 2) {
    const tag = parts[index].trim().toUpperCase()
    const content = parts[index + 1]?.trim() || ''

    if (MAIN_SCRIPT_TAGS.has(tag)) {
      if (content) mainScriptParts.push(content)
    } else if (tag === 'METADATA & QUIZ') {
      metadata = parseMetadataContent(content)
    } else if (tag === 'CHAPTERS') {
      chaptersContent = content
    } else if (!IGNORED_TAGS.has(tag)) {
      otherSections.push({ title: tag, content })
    }
  }

  const sections = []
  if (mainScriptParts.length) {
    sections.push({
      title: 'NỘI DUNG KỊCH BẢN',
      content: mainScriptParts.join('\n\n')
    })
  }

  if (metadata?.title) {
    sections.push({ title: 'TIÊU ĐỀ VIDEO', content: metadata.title })
  }
  if (metadata?.slug) {
    sections.push({ title: 'URL SLUG', content: metadata.slug })
  }

  const descriptionSection = buildDescriptionSection(metadata || {}, chaptersContent)
  if (descriptionSection) sections.push(descriptionSection)

  if (metadata?.quiz) {
    sections.push({ title: 'QUIZ', content: metadata.quiz })
  }
  if (metadata?.pinnedComment) {
    sections.push({ title: 'BÌNH LUẬN GHIM', content: metadata.pinnedComment })
  }

  sections.push(...otherSections)

  if (!sections.length && text.trim()) {
    sections.push({ title: 'KẾT QUẢ TRẢ VỀ', content: text.trim() })
  }

  return sections
}
