function formatDuration(seconds) {
  const totalMinutes = Math.max(0, Math.round((Number(seconds) || 0) / 60))
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return hours > 0 ? `${hours} giờ ${minutes} phút` : `${minutes} phút`
}

function AudioReviewPanel({
  review,
  loading,
  submitting,
  voiceName,
  hasAudio
}) {
  if (loading && !review) {
    return <div className="result-panel" style={{ padding: '18px 20px' }}>Đang kiểm duyệt kịch bản...</div>
  }
  if (!review) return null

  const metrics = review.metrics || {}
  const errors = Array.isArray(review.errors) ? review.errors : []
  const warnings = Array.isArray(review.warnings) ? review.warnings : []
  const approved = review.status === 'approved'
  const blocked = review.status === 'blocked' || !review.can_approve
  const statusColor = approved ? '#2ecc71' : blocked ? '#ff6b6b' : '#f5b041'
  const statusLabel = approved
    ? 'Đã tự động duyệt'
    : blocked ? 'Tự động kiểm tra không đạt' : 'Đang chờ hệ thống xử lý'

  return (
    <div className="result-panel" style={{ padding: '18px 20px', borderColor: `${statusColor}66` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '14px', flexWrap: 'wrap' }}>
        <div>
          <div style={{ color: 'var(--accent)', fontWeight: '700' }}>🔎 Tự động kiểm tra trước khi tạo audio</div>
          <div style={{ color: '#aaa', fontSize: '0.82em', marginTop: '5px' }}>
            Phiên bản nội dung hiện tại · Giọng: {voiceName || 'Giọng đã chọn'}
          </div>
        </div>
        <span style={{ color: statusColor, border: `1px solid ${statusColor}`, borderRadius: '12px', padding: '3px 10px', fontSize: '0.8em', fontWeight: '700' }}>
          {statusLabel}
        </span>
      </div>

      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', color: '#ddd', marginTop: '14px', fontSize: '0.88em' }}>
        <span>📝 {(metrics.word_count || 0).toLocaleString('vi-VN')} từ</span>
        <span>🔤 {(metrics.character_count || 0).toLocaleString('vi-VN')} ký tự</span>
        <span>
          ⏱ Ước tính {formatDuration(metrics.estimated_min_seconds)}–{formatDuration(metrics.estimated_max_seconds)}
        </span>
      </div>
      <div style={{ color: '#777', fontSize: '0.76em', marginTop: '6px' }}>
        Thời lượng chỉ là ước tính theo tốc độ đọc, không phải ngưỡng bắt buộc.
      </div>

      {errors.length > 0 && (
        <div style={{ marginTop: '13px', color: '#ff7b7b', fontSize: '0.86em' }}>
          {errors.map((item, index) => (
            <div key={`${item.code}-${index}`} style={{ marginTop: '4px' }}>
              ⛔ {item.message}
            </div>
          ))}
        </div>
      )}
      {warnings.length > 0 && (
        <details style={{ marginTop: '13px', color: '#f5b041', fontSize: '0.84em' }}>
          <summary>{warnings.length} cảnh báo cần xem lại</summary>
          {warnings.map((item, index) => (
            <div key={`${item.code}-${index}`} style={{ marginTop: '5px' }}>
              ⚠️ {item.message}
            </div>
          ))}
        </details>
      )}

      <div style={{ color: '#888', fontSize: '0.8em', marginTop: '13px' }}>
        {submitting
          ? 'Hệ thống đang gửi yêu cầu audio an toàn sang Genmax...'
          : hasAudio
            ? 'Audio của nội dung này đã có trên hệ thống.'
            : blocked
              ? 'Genmax chưa được gọi nên chưa phát sinh credit.'
              : 'Khi đạt kiểm tra, hệ thống tự duyệt và tạo audio; bạn không cần xác nhận.'}
      </div>
    </div>
  )
}

export default AudioReviewPanel
