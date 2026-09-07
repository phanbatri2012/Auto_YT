import assert from 'node:assert/strict'
import test from 'node:test'

import { parseVideoSections } from './videoSections.js'

test('separates unlabeled metadata and keeps chapters in the description group', () => {
  const result = parseVideoSections(`
### [INTRO]
Mở đầu.
### [BODY]
Nội dung chính.
### [METADATA & QUIZ]
TIÊU ĐỀ: SỰ THẬT LỊCH SỬ

SLUG: su-that-lich-su

Đây là phần mô tả đầy đủ của video.

#LichSu #VietNam

Theo quý vị, bài học lớn nhất là gì?
💬 Hãy chia sẻ ý kiến trong phần bình luận.

Theo Quý vị, sự kiện nào diễn ra trước?
A. Sự kiện A
B. Sự kiện B
C. Sự kiện C
D. Sự kiện D

Đáp án đúng: A
Giải thích: Nội dung giải thích.
### [CHAPTERS]
00:00 Mở đầu
03:15 Diễn biến chính
`)

  assert.deepEqual(result.map(section => section.title), [
    'NỘI DUNG KỊCH BẢN',
    'TIÊU ĐỀ VIDEO',
    'URL SLUG',
    'MÔ TẢ, TAG & CHAPTERS',
    'QUIZ',
    'BÌNH LUẬN GHIM'
  ])
  assert.equal(result[1].content, 'SỰ THẬT LỊCH SỬ')
  assert.equal(result[2].content, 'su-that-lich-su')
  assert.match(result[3].content, /^Đây là phần mô tả/)
  assert.match(result[3].content, /#LichSu #VietNam/)
  assert.match(result[3].content, /00:00 Mở đầu/)
  assert.doesNotMatch(result[3].content, /^(?:MÔ TẢ|TAG|CHAPTERS):/m)
  assert.match(result[4].content, /Theo Quý vị, sự kiện nào/)
  assert.doesNotMatch(result[4].content, /bài học lớn nhất/)
  assert.match(result[5].content, /bài học lớn nhất/)
})

test('supports explicitly labeled metadata fields', () => {
  const result = parseVideoSections(`
### [METADATA & QUIZ]
**Tiêu đề video:** Tiêu đề mới
**URL SLUG:** tieu-de-moi
**Mô tả:** Mô tả video
**Tags:** #TagMot #TagHai
**Bình luận ghim:** Nội dung bình luận
**QUIZ:** Câu hỏi kiểm tra?
A) Một
B) Hai
### [CHAPTERS]
00:00 Bắt đầu
`)

  assert.equal(result.find(section => section.title === 'TIÊU ĐỀ VIDEO')?.content, 'Tiêu đề mới')
  assert.equal(result.find(section => section.title === 'URL SLUG')?.content, 'tieu-de-moi')
  assert.match(result.find(section => section.title === 'MÔ TẢ, TAG & CHAPTERS')?.content, /#TagMot/)
  assert.match(result.find(section => section.title === 'QUIZ')?.content, /Câu hỏi kiểm tra/)
  assert.equal(result.find(section => section.title === 'BÌNH LUẬN GHIM')?.content, 'Nội dung bình luận')
})

test('shows chapters even when the metadata response has no description label', () => {
  const result = parseVideoSections(`
### [METADATA & QUIZ]
TIÊU ĐỀ: Video thiếu mô tả
SLUG: video-thieu-mo-ta
### [CHAPTERS]
00:00 Mở đầu
05:00 Kết luận
`)

  const combinedSection = result.find(section => section.title === 'MÔ TẢ, TAG & CHAPTERS')
  assert.ok(combinedSection)
  assert.match(combinedSection.content, /^00:00 Mở đầu/)
})
