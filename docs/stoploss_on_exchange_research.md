# Nghiên cứu: `stoploss_on_exchange` cho TradeMind (Binance spot, live)

- Ngày: 2026-09-26
- Phạm vi: chỉ đọc code và tài liệu. Chưa sửa code, chưa commit, chưa truy cập VPS.
- Phiên bản Freqtrade dùng để phân tích: **2026.8**, là release `stable` mới nhất tính đến ngày viết (phát hành 2026-08-31). Xem mục 0.1 về chuyện phiên bản này **chưa được xác minh** trên VPS.

---

## 0. Tóm tắt

**Khuyến nghị:** bật `stoploss_on_exchange` (Binance spot sẽ dùng lệnh `STOP_LOSS_LIMIT`), đặt `stoploss_on_exchange_limit_ratio` ≈ `0.98`, giữ nguyên `stoploss_on_exchange_interval = 60`. Cấu hình qua `order_types` trong `config.json.tpl`, có cờ env `STOPLOSS_ON_EXCHANGE` để rollback bằng cách restart thay vì rebuild. Làm thêm 3 việc nhỏ đi kèm, tách thành các commit riêng:

1. Pin image Freqtrade theo đúng phiên bản đang chạy, thay cho `:stable`.
2. Cho reconciliation ghi `exit_reason` và `r_multiple`. Lý do: nếu bot khởi động lại sau downtime và phát hiện stop đã khớp, bot **không gửi webhook `exit_fill`** (xem 3.5).
3. Thêm heartbeat/dead-man alert từ bên ngoài VPS.

**Điểm quan trọng nhất cần hiểu trước khi bật:**

- Khi bật `stoploss_on_exchange` ở chế độ live, Freqtrade **tắt hoàn toàn việc tự kiểm tra stop phía bot** (`interface.py`, `ft_stoploss_reached`). Mọi stop (ATR stop lẫn trailing) chỉ còn do lệnh trên Binance thực thi.
- Binance spot trong Freqtrade **chỉ hỗ trợ stop-limit**, không có stop-market. Vì vậy khi giá gap mạnh xuống dưới mức limit, lệnh có thể không khớp.
- Có ba lớp dự phòng cho tình huống không khớp:
  - Bot cancel rồi đặt lại stop mỗi khi trailing nâng mức stop. Nếu lúc đặt lại giá đã nằm dưới stop, Binance từ chối lệnh và Freqtrade chuyển sang `emergency_exit` bằng market.
  - `hard_loss_cut` (-1.5%) của LLM, chạy mỗi giờ, gọi `forceexit` market. Lệnh này hủy stop trước rồi mới bán.
  - Operator can thiệp tay.
- Không có rủi ro "bán hai lần": trên spot, lệnh stop khóa (lock) số coin, nên `forceexit` không thể bán cùng số coin đó song song.

---

## 0.1 Những gì CHƯA xác minh được

| Hạng mục | Lý do | Cách xác minh |
|---|---|---|
| Phiên bản Freqtrade đang chạy trên VPS | `freqtrade/Dockerfile` dùng `FROM freqtradeorg/freqtrade:stable` (không pin). Comment trong `freqtrade_client.py:52` ghi "Freqtrade 2026.6". Image được rebuild ngày 09-08, nên nhiều khả năng là 2026.8, nhưng chưa kiểm chứng | `docker compose exec freqtrade freqtrade --version` |
| Hành vi thực tế của Binance khi hủy một stop đã khớp (mã lỗi -2011 → ccxt `OrderNotFound` → Freqtrade `InvalidOrderException`) | Suy ra từ code ccxt/Freqtrade, chưa test trên sàn | Quan sát log khi xảy ra |
| HTTP status mà Freqtrade REST API trả về khi `forceexit` gặp `DependencyException` ("Not enough amount to exit trade") | Chưa đọc tầng `api_server` | Test trên dry-run hoặc đọc `rpc/api_server/api_v1.py` |
| Xử lý partial fill của stop-limit, sau đó `forceexit` phần còn lại: payload `exit_fill` (amount, profit) có khớp với cách TradeMind ghi sổ không | Đường code phức tạp (`recalc_trade_from_orders`), chưa trace hết | Test tích hợp hoặc quan sát |
| Weight chính xác của `GET /api/v3/order` trên Binance | Không fetch trang rate-limit | Binance docs |
| Tỷ lệ stop khớp thật của các exit hiện tại (bot-side stop đang dùng lệnh `limit`, xem 3.7) | Cần dữ liệu ledger trên VPS | Query `tradesv3.sqlite` |

---

## 1. Cơ chế `stoploss_on_exchange` trong Freqtrade 2026.8 trên Binance spot

Nguồn tài liệu:
- https://www.freqtrade.io/en/stable/stoploss/
- https://www.freqtrade.io/en/stable/configuration/#understand-order_types
- https://www.freqtrade.io/en/stable/exchanges/#binance

Nguồn code: https://github.com/freqtrade/freqtrade/tree/2026.8. Số dòng bên dưới lấy theo tag 2026.8.

### 1.1 Loại lệnh
- `freqtrade/exchange/binance.py:35-40` (spot `_ft_has`):
  - `"stoploss_order_types": {"limit": "stop_loss_limit"}`: spot **chỉ có limit**.
  - `"stoploss_blocks_assets": True`: lệnh stop khóa số coin.
- Futures có thêm `stop_market` (dòng 57), nhưng không áp dụng cho hệ thống này.
- Tài liệu exchanges ghi: "Binance supports `stoploss_on_exchange` and uses `stop-loss-limit` orders."
- API Binance spot thực ra **có** hỗ trợ `STOP_LOSS` (market khi trigger), xem https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints. Tuy vậy Freqtrade không map loại lệnh này cho spot, nên không dùng được nếu không patch Freqtrade.
- Stop trên spot trigger theo giá khớp gần nhất (last price). `stoploss_price_type` **chỉ áp dụng cho futures**. Trên spot tham số này bị bỏ qua và không được validate (tài liệu stoploss, mục "stoploss_price_type").

### 1.2 Giá stop và giá limit
- `exchange.py:1550-1570` `_get_stop_limit_rate`: `limit = stop_price * stoploss_on_exchange_limit_ratio`, mặc định `0.99`. Ví dụ stop 95 → limit 94.05. Lệnh chỉ khớp trong khoảng [limit, stop].
- `exchange.py:1579-1679` `create_stoploss`:
  - Trong dry-run, lệnh stop được mô phỏng (dòng 1615-1626).
  - Nếu Binance trả lỗi `InvalidOrder`/`BadRequest` (ví dụ "Order would trigger immediately"), code ném `InvalidOrderException`.
- `freqtradebot.py:1431-1465` `create_stoploss_order` xử lý lỗi như sau:
  - `InvalidOrderException` → `emergency_exit` (market).
  - `InsufficientFundsError` → `handle_insufficient_funds`.
  - Các `ExchangeError` khác → chỉ log, vòng sau thử lại.

### 1.3 Thời điểm đặt lệnh
- `freqtradebot.py:1315-1350` `exit_positions`: mỗi vòng `process()` gọi `handle_stoploss_on_exchange(trade)` cho từng trade đang mở. Chu kỳ vòng là `internals.process_throttle_secs = 5`, lấy từ `config.json.tpl`.
- `freqtradebot.py:1467-1521` `handle_stoploss_on_exchange`:
  1. Với mỗi lệnh stop đang mở, gọi `fetch_stoploss_order`. Tức là **khoảng 5 giây gọi một request REST cho mỗi trade**.
  2. Nếu status là `closed`/`triggered`: gán `exit_reason = "stoploss_on_exchange"`, gọi `_notify_exit(trade, "stoploss", True)` (gửi webhook **`exit_fill`**), trade đóng.
  3. Nếu trade có lệnh non-stop đang mở (ví dụ lệnh exit ROI) và `stoploss_blocks_assets` → **không đặt stop** (dòng 1502-1507).
  4. Nếu chưa có stop → tạo stop tại `trade.stoploss_or_liquidation`.
  5. Ngược lại → `manage_trade_stoploss_orders` (dòng 1523-1562): nếu tất cả stop đã bị hủy thì tạo lại. Nếu `config["use_custom_stoploss"]` hoặc `trailing_stop` bật → `handle_trailing_stoploss_on_exchange`.

### 1.4 Tương tác với `custom_stoploss` và trailing
- `freqtradebot.py:1564-1600` `handle_trailing_stoploss_on_exchange`:
  - Nếu `trade.stop_loss` (sau khi làm tròn) **cao hơn** `stopPrice` của lệnh đang nằm trên sàn (`exchange.py:1526-1537` `stoploss_adjust`) **và** lệnh stop hiện tại đã có tuổi ≥ `stoploss_on_exchange_interval` giây (`trade.stoploss_last_update_utc` = `order_date` của lệnh stop mở mới nhất, `trade_model.py:539-542`), thì Freqtrade **cancel lệnh cũ rồi tạo lệnh mới**.
  - Hệ quả: **mỗi trade bị cancel+replace tối đa một lần mỗi 60 giây**, và chỉ khi mức stop tăng. Stop không bao giờ bị hạ (`Trade.adjust_stop_loss`, `trade_model.py:839-908`).
- `custom_stoploss` vẫn được gọi mỗi vòng, qua `handle_trade → should_exit → ft_stoploss_reached → ft_stoploss_adjust` (`interface.py:1419+`, `1520-1603`). Việc này cập nhật `trade.stop_loss` trong DB, sau đó `handle_trailing_stoploss_on_exchange` đồng bộ mức mới lên sàn.
- **Bot-side stop bị tắt ở chế độ live** (`interface.py:1649-1653`):
  ```python
  if (sl_higher_long or sl_lower_short) and (
      not self.order_types.get("stoploss_on_exchange") or self.config["dry_run"]
  ):
      exit_type = ExitType.STOP_LOSS
  ```
  Khi live và bật on-exchange, bot **không bao giờ** tự ra lệnh thoát vì stop. Ở dry-run thì cả hai cơ chế cùng chạy.
- **Đặt stop ngay khi khớp lệnh vào, ở đúng mức ATR:**
  - `resolvers/strategy_resolver.py:240-244`: nếu chữ ký `custom_stoploss` có tham số `after_fill` thì `_ft_stop_uses_after_fill = True`. `ExternalSignalStrategy.custom_stoploss` **có** tham số này (`ExternalSignalStrategy.py:145-154`).
  - Do đó, sau khi entry khớp, `freqtradebot.py:2423-2430` gọi `ft_stoploss_adjust(..., after_fill=True)`, và `trade.stop_loss` được đặt ngay ở mức ATR stop (từ tag `slpct:`), không phải -8%.
  - Lệnh stop đầu tiên trên sàn vì vậy nằm đúng mức ATR. **Tham số `after_fill` phải được giữ trong chữ ký hàm** (đề xuất một test bảo vệ, mục 5.3).

### 1.5 Rate limit
- Ước tính với tối đa 6 trade mở:
  - Mỗi 5 giây một `GET /api/v3/order` cho mỗi trade → khoảng 72 request/phút. Với weight khoảng 4 thì ≈ 288 weight/phút. Hạn mức REQUEST_WEIGHT của Binance là 6000/phút.
  - Cancel+replace tối đa 1 lần/phút/trade.
  - → Không có vấn đề về rate limit.
- Tài liệu Freqtrade cảnh báo không nên giảm `stoploss_on_exchange_interval` vì có thể bị sàn ban. **Giữ 60**.
- Filter `MAX_NUM_ALGO_ORDERS` (thường 5 lệnh/symbol, tính cả STOP_LOSS_LIMIT) không phải vấn đề: mỗi pair chỉ có một trade, mỗi trade một lệnh stop (https://developers.binance.com/docs/binance-spot-api-docs/filters).

---

## 2. Tương thích với TradeMind

### 2.1 ATR stop theo từng trade (`custom_stoploss`)
Tương thích (xem 1.4). Mức stop trên sàn bằng đúng `open_rate * (1 - slpct)`, sau đó tăng theo trailing (2%/1.5% so với `max_rate`, `ExternalSignalStrategy.py:192-195`). Có hai khác biệt so với hiện tại:
- **Độ trễ trailing:** hiện tại bot kiểm tra mỗi 5 giây theo giá bid/ask. Khi bật on-exchange, mức stop trên sàn có thể chậm tới 60 giây so với mức trailing tính trong DB. Mức cũ luôn *thấp hơn* nên an toàn theo nghĩa không bao giờ lỏng hơn stop ATR, nhưng có thể trả lại thêm một ít lợi nhuận.
- **Trailing "đã vượt":** nếu lúc replace giá đã nằm dưới mức stop mới, Binance từ chối lệnh ("would trigger immediately"). Freqtrade khi đó `emergency_exit` bằng market (`freqtradebot.py:1459-1462`), tương đương bot-side stop hiện tại nhưng bằng lệnh market.

### 2.2 `minimal_roi`
- ROI vẫn do bot xử lý (`handle_trade`) và đặt lệnh exit `limit` (mặc định `order_types.exit`).
- `execute_trade_exit` **hủy stop trên sàn trước** (`freqtradebot.py:2145`, `cancel_stoploss_on_exchange(trade, allow_nonblocking=True)`; spot Binance bị block nên luôn hủy).
- **Khoảng hở:** trong thời gian lệnh ROI dạng limit chưa khớp (tối đa `unfilledtimeout.exit = 10` phút), trade **không có stop trên sàn** (1.3 bước 3) **và cũng không có bot-side stop** (1.4). Nếu lệnh ROI timeout thì bị hủy và stop được đặt lại ở vòng sau.
- Rủi ro này nhỏ, vì ROI chỉ bắn khi trade đang lãi ≥ 0.5%. Muốn đóng hẳn khoảng hở thì chuyển `order_types.exit` sang `market`. Việc đó bắt buộc `exit_pricing.price_side = "other"` (`config_validation.py:122-125`) và là thay đổi hành vi riêng, **không gộp** vào đợt này.

### 2.3 `forceexit` từ risk_engine (LLM SELL / `hard_loss_cut`)
- Đường đi: `freqtrade_client.py:75-90` (`ordertype: "market"`, retry 3 lần × 1s) → Freqtrade `rpc.py:1087-1132` `_rpc_force_exit` (giữ `_exit_lock`) → `__exec_force_exit` (`rpc.py:1023+`) → `execute_trade_exit`.
- **Hủy stop trước khi bán: có.** `execute_trade_exit` gọi `cancel_stoploss_on_exchange` (dòng 2145), rồi `_safe_exit_amount` gọi `wallets.update()` để coin vừa được unlock hiện ra (dòng 2059-2060), sau đó mới đặt lệnh market.
- `trade.open_orders` / `has_open_orders` **không tính lệnh stop** (`trade_model.py:591-605`). Vì vậy `__exec_force_exit` không bị chặn bởi stop đang nằm trên sàn.
- `_exit_lock` bao cả `exit_positions` (`freqtradebot.py:291-298`), nên luồng quản lý stop của bot và `forceexit` không chạy song song.
- **Race khi stop khớp trên sàn trong ≤5 giây trước lúc bot poll, đúng lúc `forceexit` tới:**
  1. `cancel_stoploss_order` bị Binance trả "Unknown order". `cancel_stoploss_on_exchange` bắt `InvalidOrderException` và chỉ log (dòng 1106-1115).
  2. `_safe_exit_amount`: ví gần như không còn coin → `DependencyException("Not enough amount to exit trade")`.
  3. Freqtrade API trả lỗi. risk_engine retry 3 lần rồi ghi `Order(SELL, FAILED)` + `ORDER_FAILED` (`main.py:421-440`). `ORDER_FAILED` **không** được relay lên Telegram (`notifier/app/main.py:32-42`).
  4. ≤5 giây sau, `handle_stoploss_on_exchange` thấy stop `closed` → webhook `exit_fill` với `exit_reason="stoploss_on_exchange"`. `webhooks.py::_find_order` (dòng 51-66) tìm Order SELL theo `freqtrade_trade_id` **không lọc status**, nên lấy đúng Order FAILED vừa tạo, chuyển sang FILLED rồi đóng position (dòng 247-321) → `POSITION_CLOSED` → Telegram.
  5. **Kết quả:** không có lệnh bán kép, vì spot không thể bán coin không có. Sổ sách cuối cùng đúng. Chỉ có một Order từng mang trạng thái FAILED trong audit, không có hại.
  - Lưu ý: bước 2-3 dựa trên đọc code, **chưa kiểm chứng** mã HTTP thực tế (mục 0.1).
- **Race ngược lại (không thể xảy ra):** `forceexit` đã hủy stop và lệnh market đang chạy, rồi bot vòng sau đặt lại stop. `handle_stoploss_on_exchange` không đặt stop khi `trade.has_open_orders` (dòng 1502-1507), và lệnh market thường khớp ngay (dòng 2195-2196).
- **Hủy stop thất bại vì lý do khác (ví dụ mạng):** `ExchangeError`/`TemporaryError` từ cancel **không** bị bắt trong `cancel_stoploss_on_exchange` (chỉ bắt `InvalidOrderException`), nên propagate lên API → risk_engine thấy lỗi → retry → FAILED. Đây là fail-closed, không bán mù.

### 2.4 dry_run và live
- Dry-run:
  - Lệnh stop được mô phỏng (`exchange.py:1615-1626`).
  - Bot-side stop **vẫn chạy** (`interface.py:1652`).
  - Tài liệu ghi: "In combination with `stoploss_on_exchange`, the stop_loss price is assumed to be filled".
  - `startup_update_open_orders` bị bỏ qua (`freqtradebot.py:407-409`).
- Hệ quả: **dry-run không kiểm chứng được** các yếu tố quan trọng như trượt giá, không khớp, lock balance, hay race. Chỉ kiểm chứng được là config hợp lệ và bot khởi động được.
- Hệ thống đang chạy live (`DRY_RUN=false`). Thay đổi này **không đụng** tới `DRY_RUN` (AGENTS.md mục 7, PROJECT.md 14.13).

### 2.5 Reconciliation, audit và Telegram khi stop khớp trên sàn
- **Bot đang sống khi stop khớp:** webhook `exit_fill` với `exit_reason="stoploss_on_exchange"` → `webhooks.py:198-321`:
  - Không có Order SELL nào nên code tạo Order tổng hợp (synthetic, dòng 212-245).
  - Position CLOSED, có `exit_reason`, `r_multiple`, `fees_usdt`.
  - Tạo `ORDER_FILLED` + `POSITION_CLOSED` → notifier relay "SELL: … pnl_usdt=…".
  - Đường này **đã hoạt động** (giống exit ROI/stop hiện tại).
- **Bot chết khi stop khớp, sau đó bot sống lại:** `startup_update_open_orders` (`freqtradebot.py:402-447`) fetch lệnh stop → `update_trade_state(..., stoploss_order=True)` → trade đóng với `exit_reason="stoploss_on_exchange"` (`trade_model.py:943-945`). Tuy nhiên `order_close_notify` **không gửi thông báo** cho lệnh stop (`freqtradebot.py:2441-2446`, điều kiện `not stoploss_order`), nên **không có webhook `exit_fill`**.
  - TradeMind dựa vào `reconciliation.py:87-179` `_reconcile_open_positions` (chu kỳ 60s): phát hiện `is_open=False` → đóng position, tạo `POSITION_CLOSED` (source `position_reconciliation`) → Telegram. Luồng này **hoạt động**, nhưng:
    - không ghi `position.exit_reason` (schema `FreqtradeTrade` ở `schemas.py:93-110` chưa có trường này);
    - không tính `r_multiple` / `fees_usdt`, nên trade bị **loại khỏi mọi metric R/expectancy** (PROJECT.md 9.5, plan D5).
  - Với một hệ thống vừa có sự cố VPS, đây chính là trường hợp stop-on-exchange phát huy tác dụng. Vì vậy nên bổ sung (mục 5.2, commit riêng).
- **Không ảnh hưởng tới reconciliation entry:** `_reconcile_open_entry` yêu cầu `has_open_orders is False` (`reconciliation.py:189`). Freqtrade tính `has_open_orders` **không gồm** lệnh stop (`trade_model.py:598-605`), nên một stop đang nằm trên sàn không gây alert `entry_not_confirmed_filled`.
- **Không ảnh hưởng tới sizing:** stop SELL khóa coin gốc (BTC/ETH/…), không khóa USDT. `get_account_balance` (`freqtrade_client.py:100-135`) dùng `total` và USDT `free`, nên equity và free balance không đổi.
- **Nhãn exit:** mọi lần stop khớp trên sàn đều mang `exit_reason="stoploss_on_exchange"`. Nhãn này không phân biệt ATR stop với trailing, trong khi bot-side hiện tại cũng đã gắn `trailing_stop_loss` cho gần như mọi stop vì ATR stop luôn "cao hơn" mức -8% ban đầu. Trong repo không có code nào lọc theo chuỗi exit_reason cụ thể (chỉ có test). PROJECT.md 7.4 cần bổ sung giá trị `stoploss_on_exchange` (và `emergency_exit`).

---

## 3. Các failure mode

### 3.1 Stop-limit không khớp khi giá gap nhanh
- Giá xuyên qua `stop` rồi xuống dưới `limit = stop × ratio` trước khi lệnh limit khớp → lệnh nằm treo dưới dạng limit sell trên sổ.
- **Không có** fallback bot-side (1.4).
- Các lối thoát:
  - (a) Giá hồi lên ≥ limit thì khớp.
  - (b) Trailing nâng stop → replace → "would trigger immediately" → `emergency_exit` market. Chỉ xảy ra khi trade đã từng lãi ≥2%.
  - (c) Cycle LLM hằng giờ thấy PnL ≤ -1.5% → `hard_loss_cut` → `forceexit` market (hủy lệnh treo rồi bán).
  - (d) Operator.
- Với trade chỉ có ATR stop (không trailing), độ trễ tệ nhất tới lối thoát (c) là **≤ ~1 giờ**, cộng thêm nếu LLM đang outage (pre-filter vẫn có thể bắt `hard_loss_cut`; cần xác nhận hành vi `deterministic:prefilter` khi PnL vượt ngưỡng, xem `validators/prefilter.py`).
- Giảm thiểu: hạ `limit_ratio` xuống `0.98`, tức chấp nhận khớp tới 2% dưới stop. Với stop ATR tối thiểu 1.5%, trượt thêm tối đa 2% ≈ 1.3R trong trường hợp xấu nhất. Đổi lại xác suất khớp khi biến động mạnh cao hơn nhiều. **Đây là quyết định cần người dùng chọn** (0.99 / 0.98 / 0.97).
- Filter `PERCENT_PRICE_BY_SIDE` của Binance cho phép giá limit lệch khá xa giá trung bình, nên 0.97-0.99 không vướng.

### 3.2 Min notional / lot size với stake ~$56
- Binance `NOTIONAL` min cho các cặp USDT chính hiện là cỡ 5 USDT. Lệnh stop có notional ≈ amount × limit ≈ $54, dư nhiều.
- `amount_to_precision` / `price_to_precision` xử lý `LOT_SIZE` và tick size (`exchange.py:1609-1613, 1640`).
- Rủi ro thực tế nằm ở **partial fill**: nếu stop khớp một phần và phần còn lại < min notional thì không bán được, thành "dust". Freqtrade có `_safe_exit_amount` với dung sai 2% (dòng 2072-2077). Dust nhỏ hơn thì cần xử lý tay. Mức độ: thấp.

### 3.3 Partial fill
- Stop khớp một phần rồi bị hủy (do trailing hoặc `forceexit`): `cancel_stoploss_on_exchange` → `update_trade_state(stoploss_order=True)` với lệnh đã khớp một phần → `trade.update_trade` → `recalc_trade_from_orders` giảm `trade.amount` (partial exit). Sau đó stop mới hoặc lệnh market xử lý phần còn lại.
- Thông báo `exit_fill` cho phần khớp một phần **không** được gửi (vì là lệnh stop). Payload `exit_fill` cuối cùng có `amount` chỉ là phần còn lại. TradeMind ghi `filled_amount = payload.amount` (`webhooks.py:248`), nên có thể lệch so với `position.amount`, trong khi PnL (`profit_amount`) là của cả trade.
- **Chưa kiểm chứng đầy đủ**, mức độ: thấp (hiếm với lot $56 trên BTC/ETH/SOL/XRP).

### 3.4 Lệnh stop mồ côi khi bot restart, redeploy hoặc migrate
- `cancel_open_orders_on_exit: false` (config hiện tại) → khi dừng hoặc restart container, **lệnh stop vẫn nằm trên Binance**. Đây chính là mục đích của thay đổi.
- Khi khởi động lại, `startup_update_open_orders` đồng bộ lại theo `order_id` lưu trong `tradesv3.sqlite` (volume `freqtrade_data`).
- **Nguy hiểm thật sự nằm ở khâu migrate VPS** (xem `docs/trademind_vps_downsize_migration_plan.md`):
  - Nếu snapshot `tradesv3.sqlite` được chụp **trong khi bot cũ còn chạy**, bot cũ có thể đã replace stop (trailing) sau thời điểm snapshot. Bot mới khi đó thấy lệnh cũ `canceled`, tạo lệnh mới, nhưng coin còn bị khóa bởi lệnh "mồ côi" mới hơn → `InsufficientFunds` → `handle_insufficient_funds`. Rối, cần can thiệp tay.
  - **Quy tắc: `docker compose stop freqtrade` ở máy cũ TRƯỚC khi copy DB.** Thứ tự trong plan hiện tại là copy ở bước 3 và `down` ở bước 8, nên cần sửa.
  - Nếu máy cũ **mất liên lạc nhưng có thể vẫn đang chạy** (như sự cố hôm nay), **không được** chạy bot mới với cùng API key cho tới khi chắc chắn bot cũ đã chết. Hai bot cùng quản lý một tài khoản là nguy hiểm, bất kể có stop-on-exchange hay không. Cách an toàn: revoke hoặc rotate API key cũ trên Binance trước.
  - Nếu bot mới chạy với DB **trống** thì mọi lệnh stop cũ là mồ côi, khóa coin. Cần hủy tay trên Binance.
- Hủy tay một lệnh stop trên Binance khi bot đang chạy → bot tự đặt lại lệnh mới (tài liệu configuration: "the bot will create a new stoploss order"). Muốn đóng vị thế bằng tay thì dùng `forceexit` qua Freqtrade, **không** bán trực tiếp trên app Binance (coin đang bị khóa, và bot sẽ đặt lại stop).

### 3.5 Bot sống lại sau downtime
- **Stop đã khớp trong lúc bot chết:** trade được đóng lúc startup, không có webhook. TradeMind reconcile trong ≤60s, có Telegram "SELL" nhưng thiếu `exit_reason`/R (2.5).
- **Stop chưa khớp:** tiếp tục quản lý bình thường. `custom_stoploss` tính lại (trailing dùng `trade.max_rate`; lưu ý `max_rate` không bao gồm đỉnh giá trong lúc downtime) và replace nếu mức stop cao hơn.
- **Stop đang treo sau trigger (3.1):** vòng đầu tiên chỉ replace nếu mức stop mới cao hơn. Còn lại chờ lối thoát (c)/(d).
- Các cycle SELL của LLM trong lúc downtime không có, vì risk_engine/scheduler chạy cùng VPS.

### 3.6 Sự cố đặt stop trên sàn
- **Đặt stop lỗi `InvalidOrder`** (ví dụ stop ≥ giá hiện tại ngay sau khi vào lệnh): `emergency_exit` market, tức trade bị đóng ngay. Với stop ATR ≥1.5% và entry vừa khớp thì hiếm.
- **Đặt stop lỗi tạm thời (mạng):** log rồi thử lại mỗi vòng. Trong khoảng đó **không có stop nào** (không có bot-side). Hiện chưa có cảnh báo cho trường hợp này. Đề xuất theo dõi log `Unable to place a stoploss order` qua heartbeat/alert (mục 4).

### 3.7 Quan sát phụ (ngoài phạm vi, không sửa ở đợt này)
- Hiện `order_types` mặc định có `"stoploss": "limit"` (`interface.py:95-101`) và `exit_pricing.price_side: "same"`. Nghĩa là **bot-side stop hiện tại thoát bằng lệnh limit đặt ở giá ask**, có thể không khớp khi thị trường giảm và phải chờ `unfilledtimeout` 10 phút.
- Nên đối chiếu ledger xem các exit `stop_loss`/`trailing_stop_loss` gần đây có bị trễ hoặc trượt không. Chưa kiểm chứng.

---

## 4. Các phương án thay thế

| Phương án | Bảo vệ khi VPS chết | Độ phức tạp / rủi ro | Kiến trúc (PROJECT.md 14) | Nhận xét |
|---|---|---|---|---|
| **A. `stoploss_on_exchange` native (stop-limit tại đúng mức ATR/trailing)** | Có, tại mức stop mới nhất (trễ ≤60s) | Thấp: chỉ đổi config. Rủi ro gap (3.1), không còn bot-side stop | Giữ nguyên: Freqtrade vẫn là thành phần duy nhất đặt lệnh | **Khuyến nghị** |
| B. Chỉ đặt một stop "thảm họa" rộng (ví dụ -8%) trên sàn, giữ ATR/trailing ở bot | Có, nhưng lỗ tối đa ~8% + trượt | **Freqtrade không hỗ trợ**: bật on-exchange là stop đặt đúng `trade.stop_loss` và tắt bot-side. Muốn làm phải có thành phần ngoài đặt lệnh Binance, trong khi lệnh đó khóa coin khiến exit của Freqtrade lỗi `InsufficientFunds`. Cần hook `confirm_trade_exit` để hủy lệnh ngoài, khá hacky | Vi phạm tinh thần 14.8 và AGENTS.md mục 4 (credential trade mới ngoài Freqtrade, thay đổi trust zone) | Không nên |
| C. Binance OCO (stop + take-profit) hoặc `trailingDelta` native | Có | Freqtrade không hỗ trợ OCO hay trailingDelta cho spot. Phải tự quản lý lệnh ngoài Freqtrade, cùng vấn đề khóa coin như B | Như B | Không nên |
| D. Heartbeat / dead-man alert từ bên ngoài (ví dụ healthchecks.io, hoặc cron trên máy cá nhân ping endpoint công khai) | **Không**, chỉ cảnh báo để người can thiệp (đóng vị thế trên app Binance) | Thấp | Thành phần giám sát thuộc Administration zone, không có credential sàn. Cần ghi vào PROJECT.md (dịch vụ ngoài mới) | **Bổ sung cho A**, không thay thế |
| E. Chỉ runbook (operator đóng tay trên app Binance khi VPS chết) | Phụ thuộc người, có độ trễ | Không tốn công | — | Luôn nên có |

Lưu ý với D: VPS chết thì chính nó không gửi được cảnh báo, nên heartbeat phải là kiểu **push lên dịch vụ ngoài** (thiếu ping thì dịch vụ ngoài báo), không phải kiểu VPS tự báo.

---

## 5. Khuyến nghị cụ thể

### 5.1 Commit 1: bật `stoploss_on_exchange` (thay đổi chính)

**`freqtrade/user_data/config.json.tpl`** (đề xuất, CHƯA áp dụng):
```diff
     "cancel_open_orders_on_exit": false,
     "trading_mode": "spot",
+    "order_types": {
+        "entry": "limit",
+        "exit": "limit",
+        "emergency_exit": "market",
+        "stoploss": "limit",
+        "stoploss_on_exchange": ${STOPLOSS_ON_EXCHANGE},
+        "stoploss_on_exchange_interval": 60,
+        "stoploss_on_exchange_limit_ratio": 0.98
+    },
     "unfilledtimeout": {
```
Ghi chú:
- `order_types` trong config **ghi đè toàn bộ** dict của strategy, và bắt buộc có đủ `entry`, `exit`, `stoploss`, `stoploss_on_exchange` (tài liệu configuration, mục "Understand order_types"). Các giá trị `entry`/`exit`/`stoploss` ở trên giữ **đúng mặc định hiện tại** (`interface.py:95-101`), nên không đổi hành vi entry/ROI. `force_exit` mặc định theo `exit`, còn risk_engine luôn truyền `ordertype: "market"`.
- `"stoploss": "limit"` là bắt buộc về mặt ý nghĩa: Binance spot chỉ có limit. Nếu để `"market"` thì Freqtrade tự chọn loại duy nhất có sẵn (`exchange.py:1539-1548`), nhưng để rõ ràng thì ghi `limit`.
- Giữ `"cancel_open_orders_on_exit": false` (bắt buộc, để stop sống sót qua restart hay crash).
- Cờ env giúp rollback bằng `up -d` (recreate container, entrypoint render lại config) mà không cần rebuild image.

**`freqtrade/select-db-url.sh`** hoặc `freqtrade/docker-entrypoint.sh`: validate `STOPLOSS_ON_EXCHANGE` phải là đúng `true`|`false`, giống cách `DRY_RUN` đang được validate. Nếu sai thì fail-closed, không khởi động:
```diff
+case "${STOPLOSS_ON_EXCHANGE:-}" in
+  true|false) ;;
+  *) echo "STOPLOSS_ON_EXCHANGE must be exactly 'true' or 'false'" >&2; exit 1 ;;
+esac
```

**`docker-compose.yml`** (service `freqtrade`, env) và **`.env.example`**:
```diff
+      STOPLOSS_ON_EXCHANGE: ${STOPLOSS_ON_EXCHANGE:-true}
```
```diff
+# Place Freqtrade's (ATR/trailing) stop as a resting STOP_LOSS_LIMIT on Binance
+# so open positions stay protected if the VPS/bot is down. PROJECT.md 9.2/9.4.
+STOPLOSS_ON_EXCHANGE=true
```

**`ExternalSignalStrategy.py`**: không đổi logic. Chỉ bổ sung docstring/comment rằng tham số `after_fill` bắt buộc phải giữ, vì nó giúp stop trên sàn được đặt ngay ở mức ATR khi entry khớp.

### 5.2 Commit 2 (riêng): reconciliation ghi đủ dữ liệu khi không có webhook
- `services/risk_engine/app/schemas.py::FreqtradeTrade`: thêm `exit_reason: str | None = None`. `GET /api/v1/trade/{id}` có trả trường này (`trade_model.py:753`).
- `reconciliation.py::_reconcile_open_positions`:
  - ghi `position.exit_reason = trade.exit_reason`;
  - tính `r_multiple` theo đúng cách `webhooks.py::_load_actual_risk_usdt`: đi từ `entry_order.risk_decision_id` sang `RiskDecision.actual_risk_usdt`;
  - thêm `exit_reason` vào payload `POSITION_CLOSED`.
  - Nên dùng lại helper chung thay vì copy code, ví dụ đưa lookup `actual_risk_usdt` vào `common/`, và ghi rõ đó là refactor.
- Commit này sửa một lỗ hổng có sẵn nhưng trở nên quan trọng khi bật mục 5.1.

### 5.3 Test cần có (theo AGENTS.md mục 5)
1. `services/risk_engine/tests/test_freqtrade_config.py`:
   - config render ra có `order_types` đủ 4 khóa bắt buộc;
   - `stoploss_on_exchange is True` khi `STOPLOSS_ON_EXCHANGE=true`, và False khi `false`;
   - `stoploss == "limit"`, `emergency_exit == "market"`;
   - `0.95 <= stoploss_on_exchange_limit_ratio < 1`, `stoploss_on_exchange_interval >= 60`;
   - `cancel_open_orders_on_exit is False`;
   - `entry`/`exit` giữ `limit` (không đổi hành vi ngầm).
2. Test validate env: `STOPLOSS_ON_EXCHANGE=yes` thì script trả mã khác 0 và in thông báo lỗi. Mẫu có sẵn: `test_freqtrade_rejects_invalid_dry_run_value`.
3. `test_external_signal_strategy.py`: `"after_fill" in inspect.signature(ExternalSignalStrategy.custom_stoploss).parameters` (bảo vệ 1.4).
4. `services/admin_api/tests/test_webhooks.py`:
   - `exit_fill` với `exit_reason="stoploss_on_exchange"` và không có Order SELL → tạo Order tổng hợp, position CLOSED, `POSITION_CLOSED` có `exit_reason`;
   - **race 2.3**: đã có Order SELL `FAILED` cho trade đó, rồi `exit_fill` tới → Order chuyển FILLED, position CLOSED, không tạo position hay order trùng.
5. `services/risk_engine/tests/test_reconciliation.py` (cho commit 2): trade đóng với `exit_reason="stoploss_on_exchange"` → position có `exit_reason` và `r_multiple`, payload audit có `exit_reason`.
6. Dry-run smoke (thủ công, không phải unit test): khởi động Freqtrade với `DRY_RUN=true` + `STOPLOSS_ON_EXCHANGE=true`, xác nhận bot start và log có dòng `stoploss limit order added`. Việc này chỉ chứng minh config hợp lệ (2.4).

### 5.4 Cập nhật PROJECT.md (cùng PR, theo AGENTS.md mục 6 / 14.14)
- **9.2** (đoạn "Stop-loss is a true per-trade dynamic stop…"):
  - stop được đặt thành `STOP_LOSS_LIMIT` trên Binance ngay khi entry khớp;
  - đồng bộ lên sàn mỗi ≤60s khi mức stop tăng (trailing);
  - `limit_ratio`;
  - ở live, bot không tự kiểm tra stop nữa;
  - yêu cầu tham số `after_fill`.
- **9.1.1:** `forceexit` hủy stop trên sàn trước khi bán, và mô tả race ở 2.3.
- **9.4** (bảng failure):
  - Sửa dòng "Redis unavailable" ("Freqtrade manages its own stop-loss…") thành "resting exchange stop".
  - Thêm dòng **"Freqtrade / VPS down"**: stop trên sàn vẫn bảo vệ tại mức stop cuối cùng; ROI, trailing mới và LLM exit không chạy; heartbeat alert.
  - Thêm dòng **"Stop-limit không khớp khi gap"**: lệnh treo; lối thoát là trailing replace/`emergency_exit`, `hard_loss_cut` và operator.
  - Thêm dòng **"Không có webhook khi stop khớp lúc bot offline"**: reconciliation xử lý.
- **7.4:** bổ sung `stoploss_on_exchange` và `emergency_exit` vào danh sách giá trị `exit_reason`.
- **2.2 Non-Goals:** dòng "Live trading with real funds (dry-run only)" đã lỗi thời so với thực tế vận hành. Không thuộc thay đổi này, nhưng nên ghi nhận là documentation drift.
- Nếu làm heartbeat (D): mô tả dịch vụ giám sát ngoài trong mục 3/4 và nêu rõ không có credential sàn.
- `docs/trademind_vps_downsize_migration_plan.md` và `DEPLOYMENT.md`: thêm quy tắc "stop freqtrade ở máy cũ trước khi copy `tradesv3.sqlite`", "không chạy hai bot cùng API key", và thao tác với lệnh stop mồ côi (3.4).

### 5.5 Commit riêng: pin phiên bản Freqtrade
- `FROM freqtradeorg/freqtrade:<phiên bản đang chạy>`: kiểm tra bằng `docker compose exec freqtrade freqtrade --version` trước, rồi pin **đúng** phiên bản đó để không vô tình nâng cấp.
- Toàn bộ phân tích trên dựa vào hành vi nội bộ của 2026.8 (điều kiện dry-run ở `interface.py:1652`, không có notify cho stop khi startup, `stoploss_blocks_assets`). Một bản `stable` mới có thể đổi các hành vi đó mà không ai biết.

### 5.6 Rollout và kiểm chứng trên live (lệnh để người dùng tự chạy; agent không SSH)
1. **Trước khi deploy:** chạy `make test`/`pytest` xanh trên máy local; chạy smoke dry-run như 5.3 mục 6.
2. **Chọn thời điểm:** không có trade mở (hoặc ít), thị trường yên. Bật kill switch tạm thời để không có entry mới trong lúc đổi (exit vẫn chạy, theo PROJECT.md 9.1.1).
3. **Deploy:** rebuild image freqtrade (vì template được COPY vào image), `docker compose up -d freqtrade`. Nếu có trade đang mở, vòng `process()` đầu tiên sẽ tạo stop cho nó tại `trade.stop_loss` hiện tại.
4. **Kiểm chứng:**
   - `docker compose logs freqtrade | grep -i "stoploss limit order added"`: phải thấy dòng cho mỗi trade mở, với `stop price` ≈ `stop_loss_abs`.
   - `GET /api/v1/status` (qua Freqtrade API): mỗi trade có `stop_loss_abs`, `stoploss_last_update` khác null, và trong `orders` có một order `ft_order_side == "stoploss"` trạng thái open.
   - **Binance app/web → Spot → Open Orders:** thấy lệnh *Stop-Limit* SELL, số lượng = amount của trade, Stop = `stop_loss_abs`, Limit ≈ Stop × 0.98. Coin tương ứng hiện "In order/Locked".
   - Theo dõi 1-2 trade mới từ đầu tới cuối:
     - entry khớp → stop xuất hiện trong ≤5s ở mức ATR, không phải -8%;
     - khi lãi ≥2%: log "Cancelling current stoploss on exchange … in order to add another one", lệnh stop trên Binance được nâng, tối đa 1 lần/phút;
     - khi exit ROI hoặc LLM: lệnh stop biến mất trên Binance trước khi lệnh bán khớp; Telegram "SELL" như cũ; `positions.exit_reason` đúng.
   - Kiểm tra không còn lệnh stop mồ côi sau khi trade đóng: Binance Open Orders trống cho pair đó.
5. **Tuỳ chọn, kiểm chứng tình huống VPS chết** (có kiểm soát, với một trade nhỏ): `docker compose stop freqtrade`, xác nhận lệnh stop **vẫn còn** trên Binance, rồi `start` lại và xác nhận log `Updating N open orders` và không tạo lệnh trùng.
6. **Rollback:**
   - đặt `STOPLOSS_ON_EXCHANGE=false` trong `.env` → `docker compose up -d freqtrade`;
   - **sau đó kiểm tra Binance Open Orders và hủy tay mọi lệnh Stop-Limit còn sót.** Với cờ tắt, Freqtrade không còn quản lý lệnh stop và không tự hủy chúng. Lệnh còn sót sẽ khóa coin, khiến exit của bot lỗi `InsufficientFunds`. Rủi ro này chưa kiểm chứng trong code; an toàn nhất là hủy tay.
   - Cách khác là bật kill switch, chờ hết trade mở rồi mới tắt cờ.
7. **Sau 1-2 tuần:** thống kê `exit_reason = 'stoploss_on_exchange'`, so sánh `exit_price` với mức stop để đo trượt giá thực tế. Dùng số liệu này để quyết định giữ `limit_ratio` 0.98 hay chỉnh lại.

---

## 6. Câu hỏi cần người dùng quyết định
1. **`stoploss_on_exchange_limit_ratio`:** 0.99 (mặc định, trượt tối đa 1%, dễ treo khi gap), 0.98 (đề xuất), hay 0.97?
2. Chấp nhận việc **tắt bot-side stop ở live**? Đây là hành vi cố định của Freqtrade khi bật on-exchange, không cấu hình được. Lối thoát khi lệnh treo là `hard_loss_cut` hằng giờ và operator.
3. Có làm **heartbeat/dead-man alert** từ bên ngoài (phương án D) không? Nếu có thì dùng dịch vụ nào? Đây là một phụ thuộc ngoài mới, cần ghi vào PROJECT.md.
4. Có muốn đóng khoảng hở ROI ≤10 phút (2.2) bằng cách chuyển exit sang market không? Nếu có thì làm thành thay đổi riêng, sau đợt này.
5. Sự cố hôm nay: bot trên VPS cũ **đã chắc chắn dừng** chưa? Nếu chưa chắc thì nên rotate Binance API key trước khi bật bất cứ thứ gì trên VPS mới (3.4).

## Nguồn
- Freqtrade Stoploss docs: https://www.freqtrade.io/en/stable/stoploss/
- Freqtrade Configuration, order_types và dry-run: https://www.freqtrade.io/en/stable/configuration/#understand-order_types
- Freqtrade Exchanges, Binance: https://www.freqtrade.io/en/stable/exchanges/#binance
- Freqtrade source tag 2026.8: https://github.com/freqtrade/freqtrade/tree/2026.8. Các file: `freqtrade/freqtradebot.py`, `freqtrade/strategy/interface.py`, `freqtrade/exchange/exchange.py`, `freqtrade/exchange/binance.py`, `freqtrade/persistence/trade_model.py`, `freqtrade/rpc/rpc.py`, `freqtrade/resolvers/strategy_resolver.py`, `freqtrade/configuration/config_validation.py`
- Binance Spot filters: https://developers.binance.com/docs/binance-spot-api-docs/filters
- Binance Spot trading endpoints (order types, OCO): https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints
