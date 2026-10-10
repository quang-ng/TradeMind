# Strategy lab: kết quả (2026-10-10)

**Kết luận: không chiến lược nào đạt tiêu chuẩn đã đăng ký trước.** Bộ tiêu chí mua hiện tại thua trên cả hai giai đoạn và không khác gì mua ngẫu nhiên. Ứng viên duy nhất đáng theo tiếp là họ **phá đỉnh theo xu hướng (khung 4h)**: nó hơn mua ngẫu nhiên ở cả hai giai đoạn, nhưng lợi nhuận đến từ rất ít lệnh lớn nên chưa chứng minh được.

Tham khảo: bản đăng ký trước `2026-10-10_strategy_lab_preregistration.md`, nhật ký holdout `strategy_lab_holdout_log.jsonl`. Code nằm ở `scripts/backtest/lab*.py`.

## Đã thử bao nhiêu chiến lược
- **12 chiến lược:** 7 chiến lược gốc và 5 biến thể khung 4h (phụ lục A).
- **10 chiến lược vào holdout:** 7 gốc và 3 biến thể qua luật chọn. Hai biến thể bị loại ở giai đoạn thiết kế là `trend_breakout_l55` (hơn ngẫu nhiên 0.14R, dưới ngưỡng 0.15R) và `range_reversion_rsi35` (+0.06R).
- Holdout được chạy **đúng một lần**, lúc 2026-10-10 04:44 UTC.

## Holdout (2025-10-01 → 2026-10-01, 10 cặp, phí 0.1%/chiều)

| Chiến lược | n | Tỉ lệ thắng | R trung bình | 95% CI (gom theo tuần) | Ngẫu nhiên | Hơn ngẫu nhiên | p | Quý dương | Kết quả |
|---|---|---|---|---|---|---|---|---|---|
| current_system@1h | 3126 | 36% | −0.122 | [−0.19, −0.06] | −0.111 | −0.011 | 0.99 | 0/4 | FAIL |
| trend_breakout@1h | 440 | 32% | +0.097 | [−0.34, +0.54] | −0.105 | +0.202 | 0.01 | 1/4 | FAIL |
| range_reversion@1h | 528 | 31% | −0.288 | [−0.48, −0.09] | −0.204 | −0.084 | 0.98 | 1/4 | FAIL |
| squeeze_breakout@1h | 527 | 36% | −0.087 | [−0.31, +0.13] | −0.168 | +0.081 | 0.06 | 0/4 | FAIL |
| trend_breakout@4h | 145 | 27% | +0.195 | [−0.84, +1.23] | −0.043 | +0.238 | 0.02 | 2/4 | FAIL |
| range_reversion@4h | 45 | 29% | −0.301 | [−0.64, +0.04] | −0.131 | −0.170 | 0.91 | 2/4 | FAIL |
| squeeze_breakout@4h | 155 | 30% | −0.165 | [−0.56, +0.23] | −0.160 | −0.004 | 0.49 | 3/4 | FAIL |
| trend_breakout_t45@4h | 127 | 28% | +0.350 | [−0.86, +1.56] | −0.042 | +0.392 | 0.01 | 1/4 | FAIL |
| trend_breakout_l55_t45@4h | 102 | 25% | +0.135 | [−0.82, +1.09] | −0.043 | +0.178 | 0.08 | 1/4 | FAIL |
| squeeze_breakout_r3@4h | 145 | 27% | −0.056 | [−0.57, +0.46] | −0.191 | +0.136 | 0.06 | 2/4 | FAIL |

Bảng đầy đủ của giai đoạn thiết kế nằm trong `reports/lab/design.md` và `design_variants.md` (thư mục này không commit).

## Nhận xét
1. **Hệ thống hiện tại không có lợi thế, giờ đã được xác nhận trên dữ liệu lớn:**
   - Thiết kế: −0.086R so với ngẫu nhiên −0.081R, n=3.511.
   - Holdout: −0.122R so với ngẫu nhiên −0.111R, n=3.126.
   Kết quả này khớp với issue #9.
2. **Khung 1h không có lợi thế** ở bất kỳ chiến lược nào. Phí khoảng 0.1R/lệnh ăn hết mọi khác biệt nhỏ.
3. **Phá đỉnh theo xu hướng hơn mua ngẫu nhiên một cách nhất quán:** +0.13R ở thiết kế và +0.24R ở holdout cho bản 4h gốc, p ≈ 0.02 ở cả hai. Tuy vậy nó vẫn trượt tiêu chuẩn, vì lợi nhuận **dồn vào vài lệnh và một quý**:
   - Holdout `trend_breakout@4h`: tổng +28R, nhưng **5 lệnh lớn nhất chiếm +57R**. Bỏ 5 lệnh đó thì phần còn lại trung bình −0.21R/lệnh. Trung vị −0.87R, tỉ lệ thắng 27%.
   - Theo quý (holdout): 2025Q4 +1R, 2026Q1 −7R, 2026Q2 −32R, **2026Q3 +66R**.
   - Đây là kiểu lợi nhuận điển hình của chiến lược theo xu hướng: lỗ nhỏ liên tục, thỉnh thoảng thắng rất lớn. Muốn phân biệt lợi thế thật với may mắn thì cần nhiều năm dữ liệu, không phải một năm.
4. **Thử nhiều chiến lược làm tăng rủi ro "đạt do may mắn".** Đã thử 12 chiến lược, nên một p = 0.01–0.02 đơn lẻ không đủ làm bằng chứng. Kết luận dựa trên toàn bộ tiêu chuẩn, không chỉ dựa trên p.

## Đề xuất
1. **Theo bản đăng ký trước:** không chiến lược nào đạt, nên đề xuất **dừng giao dịch tiền thật hoặc giảm rủi ro xuống mức tối thiểu**. Quyết định là của người vận hành.
2. **Kiểm tra phá đỉnh 4h trên dữ liệu chưa ai nhìn:** giai đoạn **2020–2024**. Binance có dữ liệu cho các cặp này, và phòng thí nghiệm chưa hề dùng tới. Dùng nguyên tham số và tiêu chuẩn đã chốt, không chỉnh gì thêm. Đây là cách nhanh nhất để biết lợi thế "hơn ngẫu nhiên" là thật hay không, vì có thêm 4 năm, nhiều chu kỳ tăng/giảm.
3. Nếu bước 2 đạt: chạy shadow mode từ 2026-10 trở đi trước khi đụng tới tiền thật.

## Phụ lục B: kiểm tra 2020-01-01 → 2024-10-01 (dữ liệu chưa từng nhìn, chạy một lần)

| Chiến lược | n | Tỉ lệ thắng | R trung bình | 95% CI | Ngẫu nhiên | Hơn ngẫu nhiên | p | Quý dương | Kết quả |
|---|---|---|---|---|---|---|---|---|---|
| current_system@1h (thước đo) | 15387 | 41% | −0.068 | [−0.09, −0.05] | −0.082 | +0.015 | 0.01 | 2/19 | FAIL |
| **trend_breakout@4h (ứng viên chính)** | 1093 | 34% | +0.159 | [+0.01, +0.31] | +0.067 | +0.092 | 0.01 | 9/19 | **FAIL** (hơn ngẫu nhiên < 0.15R; quý dương 9 < 15) |
| trend_breakout_t45@4h (ứng viên phụ, chọn sau holdout) | 917 | 30% | +0.538 | [+0.21, +0.86] | +0.232 | +0.307 | 0.01 | 12/19 | **FAIL** (quý dương 12 < 15) |

**R trung bình theo năm:**

| Chiến lược | 2020 | 2021 | 2022 | 2023 | 2024 |
|---|---|---|---|---|---|
| current_system | −0.04 | −0.06 | −0.08 | −0.07 | −0.11 |
| trend_breakout@4h | +0.53 | +0.19 | −0.32 | +0.09 | +0.03 |
| trend_breakout_t45@4h | +1.24 | +0.77 | −0.32 | +0.28 | +0.22 |

**Rủi ro khi chạy thật** (tổng R cộng dồn, sắp theo thời điểm đóng lệnh, 10 cặp chạy song song):

| | Sụt giảm tối đa | Chuỗi lỗ dài nhất | Quý tệ nhất | 10 lệnh lớn nhất chiếm | Bỏ 10 lệnh lớn nhất |
|---|---|---|---|---|---|
| trend_breakout@4h | −89R | 22 lệnh | −26R (2022Q3) | 70% tổng lãi | +0.05R/lệnh |
| trend_breakout_t45@4h | −77R | 32 lệnh | −36R (2022Q3) | 48% tổng lãi | +0.29R/lệnh |

### Đọc kết quả
1. **Hệ thống hiện tại:** âm ở **mọi năm** từ 2020 đến 2026 (5 năm presample cộng 2 năm lab), và không khác mua ngẫu nhiên. Đây là kết luận chắc chắn nhất của cả nghiên cứu.
2. **Ứng viên chính `trend_breakout@4h`: FAIL.**
   - Kỳ vọng dương với CI vừa chớm trên 0.
   - Nhưng chỉ hơn ngẫu nhiên 0.09R, và lãi dồn vào 2020–2021 cùng 2023Q4.
   - Giai đoạn 2022–2023 gần như đi ngang hoặc âm.
3. **Ứng viên phụ `trend_breakout_t45@4h`: FAIL, chỉ trượt ở tiêu chí quý dương** (12/19, cần 15). Các tiêu chí còn lại đều đạt và mạnh:
   - CI [+0.21, +0.86], hơn ngẫu nhiên +0.31R.
   - Hơn ngẫu nhiên ở **cả ba giai đoạn độc lập**: 2020–24 +0.31, thiết kế +0.18, holdout +0.39.
   - Bỏ 10 lệnh lớn nhất vẫn còn +0.29R/lệnh.
   - Giới hạn: biến thể này được chọn sau khi xem holdout, và lỗ trong thị trường giảm (2022 −0.32R).
   - Kết quả này phù hợp với bản chất của chiến lược theo xu hướng chỉ mua (long-only): lời trong thị trường tăng, lỗ trong thị trường giảm. Đó cũng là lý do nó không đạt tiêu chí "dương ở ≥ 75% số quý".
4. **Sụt giảm rất lớn.** Ở mức rủi ro 1%/lệnh (cấu hình hiện tại), −77R tương đương sụt khoảng −77% vốn (không tính lãi kép). Ở mức 0.25%/lệnh thì khoảng −19%.

### Đề xuất
- Theo bản đăng ký trước, **không chiến lược nào đạt**. Không có căn cứ để đưa chiến lược nào vào giao dịch tiền thật.
- Hệ thống hiện tại có bằng chứng âm nhất quán qua 7 năm. **Đề xuất mạnh: dừng giao dịch tiền thật với bộ tiêu chí hiện tại.**
- Nếu muốn tiếp tục với `trend_breakout_t45@4h`, cần một **quyết định mới**, ghi rõ là được đưa ra *sau khi* đã thấy kết quả:
  - Coi tiêu chí quý dương là không phù hợp với chiến lược theo xu hướng.
  - Chạy **shadow mode (không dùng tiền)** từ 2026-10, với tiêu chuẩn chốt trước. Ví dụ: sau 100 lệnh, hơn ngẫu nhiên ≥ 0.15R.
  - Chỉ dùng tiền thật nếu đạt, và khi đó dùng rủi ro ≤ 0.25%/lệnh, vì mức sụt giảm lịch sử rất lớn.
