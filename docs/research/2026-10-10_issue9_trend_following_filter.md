# Issue #9: Kiểm chứng bộ lọc regime `TREND_FOLLOWING` (2026-10-10)

**Kết luận: nằm trong mức nhiễu.** Các lệnh BUY bị bộ lọc chặn, nếu được vào, sẽ cho expectancy giả định khoảng **−0.05R/lệnh** (95% CI [−0.18, +0.08], n=164). Con số này gần như bằng nhóm được phép vào lệnh trong cùng giai đoạn (**−0.04R**, CI [−0.22, +0.14], n=140; Welch t = −0.09). Bộ lọc không tiết kiệm được tiền một cách đáng kể, nhưng cũng không chặn mất các lệnh thắng.

**Đề xuất (theo mốc quyết định của issue):** giữ nguyên bộ lọc. Xem lại khi M5 (expectancy gate) có đủ mẫu để thay thế nó. Không thay đổi code path nào trong issue này.

## Phương pháp

- **Dữ liệu:** 5.757 tín hiệu 1h từ bảng `signals` trên VPS, từ 2026-08-13 → 2026-10-10. Đã bỏ 10 tín hiệu 5m sinh ra trong lần bring-up ngày 2026-09-09. Kèm 58 vị thế live.
- **Phân nhóm:** dựng lại `MarketContext` từ `model_input` đã lưu, rồi chạy lại `StrategySelector` và `_entry_is_sufficient`.
  - *Bị chặn* = không có vị thế mở + đủ điều kiện entry + regime `TREND_FOLLOWING`: **1.245 tín hiệu**. Cả 1.245 tín hiệu này đều được lưu là HOLD, và số theo tuần khớp với bảng ngày 2026-10-09 trong issue (107 / 382 / 2).
  - *Được phép* = đủ điều kiện entry, regime khác: 704 tín hiệu.
- **Giả lập** (`scripts/backtest/trend_filter_study.py`): mỗi tín hiệu là một lệnh độc lập trên nến Binance.
  - Vào lệnh ở giá mở nến kế tiếp, stop ATR lấy từ `risk_engine`.
  - Cấu hình `minimal_roi`/trailing dùng **đúng bản đang chạy tại thời điểm tín hiệu**: trước PR #18 / PR #18 ×3 / mốc 10% / sau revert.
  - Áp rubric thoát lệnh mỗi nến (kể cả `hard_loss_cut`). Phí 0.1% mỗi chiều. Giữ lệnh tối đa 336h.
- **n trung thực = không chồng lệnh:** mỗi symbol chỉ có một lệnh mở trong mỗi nhóm. Tín hiệu theo từng giờ trong cùng một xu hướng thực chất là cùng một lần đặt cược: cách đếm mỗi tín hiệu một lệnh cho 1.245 lệnh, còn không chồng lệnh thì chỉ còn 164.

## Kết quả (không chồng lệnh)

| Giai đoạn | Nhóm | n | Tỉ lệ thắng | R trung bình | 95% CI | Tổng R |
|---|---|---|---|---|---|---|
| Tất cả | bị chặn | 164 | 61% | −0.049 | [−0.18, +0.08] | −8.1 |
| Tất cả | được phép | 140 | 49% | −0.039 | [−0.22, +0.14] | −5.5 |
| Sonnet (từ 2026-09-09) | bị chặn | 51 | 59% | −0.072 | [−0.32, +0.18] | −3.7 |
| Sonnet | được phép | 92 | 39% | −0.240 | [−0.44, −0.04] | −22.1 |
| qwen (2026-08-13 → 09-07) | bị chặn | 113 | 62% | −0.039 | [−0.19, +0.11] | −4.4 |
| qwen | được phép | 48 | 67% | +0.347 | [+0.02, +0.68] | +16.7 |
| **2026-09-14 → 09-27** | bị chặn | 51 | 59% | −0.072 | ±0.25 | −3.7 |
| 2026-09-14 → 09-27 | được phép | 45 | 49% | −0.078 | ±0.29 | −3.5 |

- **Sonnet** (t = +1.03): nhóm bị chặn nhỉnh hơn nhóm được phép, nhưng vẫn trong mức nhiễu.
- **qwen** (t = −2.07): nhóm được phép tốt hơn, ở ngưỡng ý nghĩa. Model và cấu hình thoát lệnh đã thay đổi từ đó, nên giai đoạn này chỉ để tham khảo.
- **Cohort 2026-09-14 → 09-27** (nhóm quan trọng nhất, ~490 tín hiệu uptrend có momentum bị chặn): nếu vào thì gần như hoà vốn, bằng hệt nhóm được phép cùng kỳ.
- **Độ nhạy:** giữ lệnh tối đa 72h hay tăng phí lên 0.15%/chiều đều không làm đổi kết luận. Mọi chênh lệch giữa hai nhóm vẫn có |t| < 1.1.

**Chia nhỏ** (n mỗi ô nhỏ, chỉ để định hướng). Không có nhóm con nào tách khỏi nhiễu một cách nhất quán:

- **Theo độ chênh EMA:** độ chênh 1.5–2.5% là +0.23R (n=20), ≥4% là −0.09R (n=110). Hướng này khớp với giả thuyết "trend đã kéo dài thì vào là đuổi giá", nhưng CI chồng lên nhau.
- **Theo confidence của model:** không đơn điệu ở cả hai nhóm. Ví dụ nhóm được phép: 0.70–0.79 là −0.49R, còn ≥0.80 là +0.35R. Như vậy đây là nhiễu, không phải tín hiệu. Từ 2026-09-26 pre-filter không gọi LLM nữa, nên các tín hiệu sau mốc đó không có confidence.
- **Theo đề xuất của model:** ở nhóm bị chặn, những tín hiệu model nói BUY cho +0.07R (n=79), những tín hiệu model nói HOLD cho −0.19R (n=36).

## Lưu ý quan trọng

1. **Giả lập khớp với live trên cùng một lệnh.** Mình ghép 39 lệnh live (có R) với đúng tín hiệu đã sinh ra chúng: live đạt −0.48R/lệnh, còn giả lập của chính các tín hiệu đó là −0.59R. Phần lớn các lệnh khớp gần như từng lệnh một. *Đính chính bản trước:* lệnh live tệ hơn nhóm được phép (−0.04R) **không phải** vì giả lập lạc quan, mà vì các lệnh thật sự được vào rơi đúng vào những đợt xấu. Những lệnh này hay mở cùng lúc trên nhiều symbol, ví dụ 2026-09-03 và 2026-09-09, nên n thực tế nhỏ hơn 39 rất nhiều.
2. **Phát hiện lớn hơn câu hỏi của issue:** ngay cả nhóm *được phép* cũng không có edge dương. Ở giai đoạn Sonnet, nó âm có ý nghĩa thống kê, và lệnh live còn tệ hơn. Bỏ bộ lọc TF sẽ không sửa được P&L, vì vấn đề nằm ở edge của rubric entry nói chung. Đây là việc của M5/M6 (#7).
3. **Sửa bug trong `scripts/backtest/ledger.py` khi làm issue này.** Khi trailing stop được nâng lên bởi đỉnh của *chính nến đó*, ledger khớp lệnh ở giá mở nến như thể giá đã gap qua stop. Hệ quả là ~186 lệnh trailing bị ghi nhận hoà vốn thay vì lãi ~+1.5%. Bug này kéo mọi nhóm xuống khoảng −0.2R và cũng ảnh hưởng tới các kết quả `mechanical_replay` / `exit_tuning_matrix` trước đây (kể cả bằng chứng walk-forward ngày 2026-08-13 của chính bộ lọc này). Các kết quả đó nên được chạy lại nếu còn dùng làm căn cứ.
4. **Giả định:** mốc deploy của PR #18 (~2026-08-31 03:30 UTC) và mốc 2026-09-05 (~03:00 UTC) là ước lượng. Mỗi lệnh chạy độc lập, không tính giới hạn exposure hay killswitch. Rubric thoát lệnh giả định model luôn đề xuất SELL, giống `mechanical_replay`.

## Tái lập

Lệnh export nằm trong `--help` của script.

```bash
.venv/bin/python scripts/backtest/trend_filter_study.py \
  --signals-jsonl reports/issue9/signals.jsonl \
  --positions-jsonl reports/issue9/positions.jsonl \
  --report-out reports/issue9/report.md --outcomes-out reports/issue9/outcomes.csv
```
