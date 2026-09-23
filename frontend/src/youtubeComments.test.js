import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { extractErrorMessage } from './apiError.js'

const commentsSource = readFileSync(
  new URL('./YouTubeComments.jsx', import.meta.url),
  'utf8'
)

test('extractErrorMessage safely formats FastAPI 422 validation error arrays', () => {
  const pydanticError = {
    detail: [
      {
        loc: ['body', 'comment_ids'],
        msg: 'List should have at most 5000 items',
        type: 'too_long'
      }
    ]
  }
  const result = extractErrorMessage(pydanticError)
  assert.equal(result, 'List should have at most 5000 items')
  assert.notEqual(result, '[object Object]')
})

test('extractErrorMessage safely formats multiple validation errors', () => {
  const multiError = {
    detail: [
      { msg: 'Lỗi trường A' },
      { msg: 'Lỗi trường B' }
    ]
  }
  const result = extractErrorMessage(multiError)
  assert.equal(result, 'Lỗi trường A; Lỗi trường B')
})

test('extractErrorMessage handles string details and error fields', () => {
  assert.equal(extractErrorMessage({ detail: 'Không có bản nháp hợp lệ để đăng.' }), 'Không có bản nháp hợp lệ để đăng.')
  assert.equal(extractErrorMessage({ error: 'Lỗi hệ thống' }), 'Lỗi hệ thống')
  assert.equal(extractErrorMessage({ message: 'Thông báo lỗi' }), 'Thông báo lỗi')
  assert.equal(extractErrorMessage('Lỗi trực tiếp'), 'Lỗi trực tiếp')
  assert.equal(extractErrorMessage(null, 'Lỗi mặc định'), 'Lỗi mặc định')
  assert.equal(extractErrorMessage({}, 'Lỗi mặc định'), 'Lỗi mặc định')
})

test('extractErrorMessage handles nested object details without [object Object]', () => {
  const nestedObj = { detail: { msg: 'Lỗi chi tiết' } }
  const result = extractErrorMessage(nestedObj)
  assert.equal(result, 'Lỗi chi tiết')
  assert.notEqual(result, '[object Object]')
})

test('YouTubeComments enforces extractErrorMessage across all API response handlers', () => {
  assert.match(commentsSource, /import\s*\{\s*extractErrorMessage\s*\}\s*from\s*'\.\/apiError'/)
  assert.match(commentsSource, /throw new Error\(extractErrorMessage\(data/)
  assert.doesNotMatch(commentsSource, /throw new Error\(data\.detail\s*\|\|/)
})
