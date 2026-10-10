# Strategy lab: bản đăng ký trước (2026-10-10)

Tài liệu này được viết **trước khi chạy bất kỳ kết quả nào**. Nó chốt lại bốn thứ: những chiến lược sẽ thử, tham số của từng chiến lược, cách chia dữ liệu và tiêu chuẩn để coi là đạt. Mục đích là để không ai, kể cả người làm nghiên cứu, có thể chỉnh luật sau khi đã nhìn thấy kết quả.

**Bối cảnh:** issue #9 cho thấy bộ tiêu chí mua hiện tại không tốt hơn mua ngẫu nhiên. Xem `2026-10-10_issue9_trend_following_filter.md`. Câu hỏi của phòng thí nghiệm: **có chiến lược nào có lợi thế thật sau phí không?**

## Dữ liệu
- **Nguồn:** nến 1h Binance spot của 10 cặp BTC, ETH, SOL, XRP, BNB, ADA, DOGE, LINK, AVAX, LTC (quote USDT). Khung 4h được gộp từ 1h.
- **Giai đoạn thiết kế:** 2024-10-01 → 2025-10-01. Được phép xem kết quả nhiều lần, thử và chỉnh.
- **Giai đoạn kiểm tra (holdout):** 2025-10-01 → 2026-10-01. **Mỗi chiến lược chỉ được xem đúng một lần.** Mỗi lần chạy holdout được ghi vào `strategy_lab_holdout_log.jsonl` (có commit). Script từ chối chạy lại cùng một chiến lược, trừ khi dùng `--allow-rerun`, và lần chạy lại đó cũng được ghi lại.
- Chỉnh một chiến lược sau khi đã xem holdout = tạo **chiến lược mới với tên mới**, và phải có holdout riêng (ví dụ dữ liệu từ sau 2026-10-01).

## Quy tắc mô phỏng (chung cho mọi chiến lược)
- Chỉ mua (long), mỗi symbol tối đa một lệnh mở. Vào lệnh ở giá mở của nến ngay sau nến ra quyết định.
- **Trong một nến:** kiểm tra stop trước target (giả định bất lợi). Nếu giá mở đã vượt qua stop thì khớp ở giá mở.
- **Trailing stop:** cập nhật ở giá đóng nến, có hiệu lực từ nến sau.
- **Thoát lệnh quyết định ở giá đóng** (tín hiệu bán, hết thời gian giữ): khớp ở giá mở nến sau.
- **Phí:** 0.1% mỗi chiều.
- **Đơn vị đo:** R = lãi/lỗ ròng chia cho rủi ro ban đầu (giá vào − stop ban đầu).
- **Nhóm đối chứng:** cùng chiến lược, cùng cách đặt stop/target/quản lý lệnh, nhưng **nến vào lệnh được chọn ngẫu nhiên**, với tần suất bằng tần suất tín hiệu của chiến lược. Chạy 100 lần với các seed khác nhau.

## Các chiến lược (tham số đã chốt)

| Chiến lược | Khung | Mua khi | Stop ban đầu | Thoát |
|---|---|---|---|---|
| `current_system` (thước đo, không phải ứng viên) | 1h | ≥3 xác nhận, có xu hướng + động lượng, regime không phải TREND_FOLLOWING | clamp(2×ATR/giá, 1.5%, 8%) dưới giá tín hiệu | Bảng ROI trước PR #18; trailing 2%/1.5%; bán theo bộ tiêu chí bán khi \|pnl\| > 0.5%; cắt lỗ cứng −1.5% ở giá đóng |
| `trend_breakout` | 1h, 4h | Giá đóng > đỉnh cao nhất 20 nến trước **và** giá đóng ngày hôm qua > EMA50 ngày | giá vào − 2×ATR | Chandelier: đỉnh từ lúc vào − 3×ATR; tối đa 10 ngày |
| `range_reversion` | 1h, 4h | \|EMA50/EMA200 gap\| < 1.5% **và** RSI14 < 30 **và** giá đóng < Bollinger dưới (20, 2) | giá vào − 1.5×ATR | Target = Bollinger giữa tại nến tín hiệu; tối đa 48 nến |
| `squeeze_breakout` | 1h, 4h | Độ rộng Bollinger (nến trước) ≤ phân vị 20% của 120 nến **và** giá đóng > Bollinger trên **và** volume > 1.5× trung bình 20 | đáy thấp nhất 20 nến trước, kẹp trong [giá vào − 3×ATR, giá vào − 1×ATR] | Target 2R; tối đa 72 nến |

`current_system` là bản xấp xỉ hệ thống live. Điểm khác: chỉ báo được tính trên toàn bộ lịch sử thay vì cửa sổ 200 nến, và không có LLM.

## Tiêu chuẩn đạt (áp dụng trên holdout)
Một chiến lược chỉ được coi là **có lợi thế** khi đạt **tất cả** các điều kiện sau:
1. **n ≥ 100 lệnh.**
2. **Khoảng tin cậy 95% của R trung bình nằm hoàn toàn trên 0.** Khoảng này được gom theo tuần vào lệnh, vì các coin di chuyển cùng nhau.
3. **R trung bình cao hơn nhóm đối chứng ít nhất 0.15R.**
4. **p < 0.05:** dưới 5% số lần chạy ngẫu nhiên có R trung bình ≥ chiến lược.
5. **R trung bình dương ở ít nhất 3/4 quý của holdout.**

## Điều gì xảy ra sau đó
- **Có chiến lược đạt:** viết thiết kế đưa nó vào hệ thống. Lộ trình: chạy shadow mode → live với `risk_per_trade_pct` 0.25% → mức bình thường, kèm điều kiện dừng đặt sẵn. Cập nhật `PROJECT.md` trong cùng PR.
- **Không chiến lược nào đạt:** kết luận rằng các chỉ báo kỹ thuật trên khung 1h/4h với 10 cặp này không tạo ra lợi thế đủ thắng phí. Đề xuất dừng giao dịch tiền thật hoặc giảm rủi ro xuống mức tối thiểu cho tới khi có giả thuyết mới.

## Phụ lục A: biến thể thử trên dữ liệu thiết kế (viết trước khi chạy biến thể, 2026-10-10)

Giai đoạn thiết kế cho thấy khung 1h không có ứng viên nào. Vì vậy biến thể chỉ thử ở **khung 4h**. Danh sách biến thể được chốt **trước khi chạy**, gồm 5 biến thể:

| Biến thể | Khác chiến lược gốc ở |
|---|---|
| `trend_breakout_l55` | phá đỉnh 55 nến thay vì 20 |
| `trend_breakout_t45` | trailing 4.5×ATR thay vì 3×ATR |
| `trend_breakout_l55_t45` | cả hai thay đổi trên |
| `squeeze_breakout_r3` | target 3R, giữ tối đa 120 nến |
| `range_reversion_rsi35` | RSI14 < 35 thay vì < 30 (để có thêm mẫu) |

**Luật chọn biến thể vào holdout** (chốt trước khi chạy): một biến thể được đưa vào holdout khi trên dữ liệu thiết kế nó **hơn mua ngẫu nhiên ≥ 0.15R và p < 0.10**.

Sau đó holdout được chạy **một lượt duy nhất** cho 7 chiến lược gốc cộng các biến thể được chọn. Tổng số chiến lược được thử, cả những biến thể không được chọn, đều được ghi trong báo cáo kết quả. Việc thử nhiều chiến lược làm tăng khả năng có một chiến lược đạt do may mắn, nên người đọc cần biết con số này.

## Phụ lục B: kiểm tra trên dữ liệu 2020–2024 (viết trước khi tải/chạy, 2026-10-10)

Holdout 2025-10 → 2026-10 đã dùng hết, và không chiến lược nào đạt. Ứng viên duy nhất đáng theo tiếp là họ phá đỉnh theo xu hướng khung 4h. Lần kiểm tra này dùng **giai đoạn trước đó, chưa từng được nhìn**: **2020-01-01 → 2024-10-01**, nhãn `presample`.

- **Ứng viên chính:** `trend_breakout@4h`, giữ nguyên tham số đã chốt ban đầu.
- **Ứng viên phụ:** `trend_breakout_t45@4h`. Lưu ý: biến thể này được chọn *sau khi* đã thấy kết quả holdout, nên kết quả của nó có độ tin cậy thấp hơn ứng viên chính.
- **Thước đo, không phải ứng viên:** `current_system@1h`.
- **Không chỉnh tham số nào.** Cùng 10 cặp, phí 0.1%/chiều, cùng quy tắc mô phỏng. Một cặp chưa niêm yết thì chỉ bắt đầu từ khi có đủ dữ liệu (SOL, AVAX lên Binance năm 2020).
- **Tiêu chuẩn đạt:** giữ nguyên 5 điều kiện. Điều kiện về quý được hiểu theo đúng tỉ lệ ban đầu: **R trung bình dương ở ≥ 75% số quý** (bản gốc là 3/4 quý). Thêm phần mô tả theo từng năm, chỉ để tham khảo.
- **Chạy một lần duy nhất** và ghi vào nhật ký như holdout.

**Đọc kết quả thế nào:** nếu ứng viên chính đạt → có cơ sở chạy shadow mode từ 2026-10. Nếu không đạt → kết luận lợi thế "hơn mua ngẫu nhiên" ở 2024–2026 chưa đủ vững để dùng tiền thật.
