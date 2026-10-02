// Vietnamese display labels for the enum-like codes the API returns
// (actions, statuses, rejection/exit reasons, regimes, audit event types,
// score-breakdown keys). Display only — the codes themselves are what the
// API accepts and filters on, so they are never translated in requests.
// Unknown codes fall back to `readable()`'s title-casing in format.ts.
export const VI_LABELS: Record<string, string> = {
  // Actions / sides
  BUY: 'MUA',
  SELL: 'BÁN',
  HOLD: 'GIỮ',
  ALL: 'Tất cả',

  // Signal / order / position status
  PENDING: 'Đang chờ',
  CONSUMED: 'Đã xử lý',
  EXPIRED: 'Hết hạn',
  VALIDATED: 'Hợp lệ',
  SUBMITTED: 'Đã gửi',
  FILLED: 'Đã khớp',
  FAILED: 'Thất bại',
  CANCELLED: 'Đã hủy',
  OPEN: 'Đang mở',
  CLOSED: 'Đã đóng',

  // Risk rejection reasons
  LOW_CONFIDENCE: 'Độ tin cậy thấp',
  STALE_SIGNAL: 'Tín hiệu quá cũ',
  SIGNAL_WAS_HOLD: 'Tín hiệu là GIỮ',
  NEGATIVE_EXPECTANCY_SETUP: 'Kỳ vọng âm',
  MAX_POSITIONS_REACHED: 'Đã đủ số vị thế tối đa',
  MAX_EXPOSURE_REACHED: 'Vượt mức vốn tối đa',
  DAILY_LOSS_LIMIT_HIT: 'Chạm giới hạn lỗ ngày',
  CONSECUTIVE_LOSS_PAUSE: 'Tạm dừng do thua liên tiếp',
  COOLDOWN_ACTIVE: 'Đang trong thời gian chờ',
  KILLSWITCH_ACTIVE: 'Đang dừng khẩn cấp',
  DUPLICATE_SIGNAL: 'Tín hiệu trùng',
  INSUFFICIENT_BALANCE: 'Không đủ số dư',
  INVALID_SIGNAL_SCHEMA: 'Tín hiệu sai định dạng',
  FREQTRADE_BALANCE_UNAVAILABLE: 'Không lấy được số dư',
  INTERNAL_ERROR: 'Lỗi nội bộ',
  NO_POSITION_TO_EXIT: 'Không có vị thế để bán',

  // Exit reasons (Freqtrade exit tags)
  ROI: 'Chốt lời (ROI)',
  MINIMAL_ROI: 'Chốt lời (ROI)',
  TAKE_PROFIT: 'Chốt lời',
  STOP_LOSS: 'Cắt lỗ',
  STOPLOSS: 'Cắt lỗ',
  ATR_STOPLOSS: 'Cắt lỗ ATR',
  STOPLOSS_ON_EXCHANGE: 'Cắt lỗ trên sàn',
  TRAILING_STOP_LOSS: 'Cắt lỗ đuổi',
  EXIT_SIGNAL: 'Tín hiệu bán',
  FORCE_EXIT: 'Bán thủ công',
  FORCE_SELL: 'Bán thủ công',
  EMERGENCY_EXIT: 'Bán khẩn cấp',
  LIQUIDATION: 'Thanh lý',

  // Setup / volatility regimes
  TREND_FOLLOWING: 'Theo xu hướng',
  TREND_PULLBACK: 'Hồi trong xu hướng',
  MOMENTUM_CONTINUATION: 'Tiếp diễn đà tăng',
  MEAN_REVERSION: 'Hồi về trung bình',
  HIGH_VOLATILITY: 'Biến động cao',
  NORMAL: 'Bình thường',
  LOW_VOLATILITY: 'Biến động thấp',
  UNKNOWN: 'Không rõ',

  // Sentiment
  FEAR: 'Sợ hãi',
  NEUTRAL: 'Trung lập',
  GREED: 'Tham lam',

  // Trade-score breakdown components
  TREND: 'Xu hướng',
  TREND_ALIGNMENT: 'Đồng thuận xu hướng',
  MOMENTUM: 'Động lượng',
  VOLUME: 'Khối lượng',
  REGIME: 'Trạng thái thị trường',
  RISK_REWARD: 'Rủi ro/lợi nhuận',
  VOLATILITY: 'Biến động',
  ASSUMED_REWARD_PCT: 'Lợi nhuận giả định (%)',
  ASSUMED_REWARD_MULTIPLE: 'Bội số lợi nhuận giả định',

  // Audit event types
  SIGNAL_RECEIVED: 'Nhận tín hiệu',
  SIGNAL_VALIDATION_FAILED: 'Tín hiệu không hợp lệ',
  RISK_APPROVED: 'Rủi ro: duyệt',
  RISK_REJECTED: 'Rủi ro: từ chối',
  ORDER_SUBMITTED: 'Đã gửi lệnh',
  ORDER_FILLED: 'Lệnh đã khớp',
  ORDER_FAILED: 'Lệnh thất bại',
  ORDER_CANCELLED: 'Lệnh đã hủy',
  POSITION_OPENED: 'Mở vị thế',
  POSITION_CLOSED: 'Đóng vị thế',
  KILLSWITCH_ENABLED: 'Bật dừng khẩn cấp',
  KILLSWITCH_DISABLED: 'Tắt dừng khẩn cấp',
  CONFIG_CHANGED: 'Thay đổi cấu hình',
  RECONCILIATION_REQUIRED: 'Cần đối soát lệnh',
  LLM_TIMEOUT_STREAK: 'LLM không phản hồi',
  LLM_TIMEOUT_RECOVERED: 'LLM đã hồi phục',
}

export function viLabel(value: string): string | undefined {
  return VI_LABELS[value.toUpperCase()]
}
