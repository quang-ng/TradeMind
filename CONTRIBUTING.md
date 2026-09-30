# Hướng dẫn tham gia dự án TradeMind

Tài liệu này hướng dẫn từ lúc clone repo đến lúc mở Pull Request (PR) đầu tiên.

> ⚠️ **Đọc trước khi code:** [`PROJECT.md`](PROJECT.md) là spec chính thức của hệ thống (kiến trúc, risk rules, phase), còn [`AGENTS.md`](AGENTS.md) là các quy tắc làm việc (coding standards, testing, safety). Hệ thống đang **giao dịch tiền thật** trên Binance — không bao giờ bật live trading, tắt risk check hay commit secret.

---

## 1. Chuẩn bị

Cài sẵn:

- [Git](https://git-scm.com/)
- [uv](https://docs.astral.sh/uv/) — quản lý Python và dependencies
- [Docker](https://www.docker.com/) — để chạy Postgres/Redis khi test
- Node.js 20+ — chỉ cần nếu sửa `frontend/`
- [GitHub CLI `gh`](https://cli.github.com/) (tuỳ chọn, giúp tạo PR nhanh)

Nhờ owner repo (`quang-ng`) add bạn làm **collaborator** trên GitHub, rồi chấp nhận lời mời qua email hoặc tại https://github.com/quang-ng/TradeMind/invitations.

Thiết lập SSH key với GitHub (nếu chưa có):

```bash
ssh-keygen -t ed25519 -C "email-cua-ban@example.com"
cat ~/.ssh/id_ed25519.pub   # copy nội dung, dán vào GitHub → Settings → SSH and GPG keys
ssh -T git@github.com       # kiểm tra: "Hi <username>! You've successfully authenticated..."
```

Cấu hình tên/email cho git (một lần):

```bash
git config --global user.name "Tên Của Bạn"
git config --global user.email "email-cua-ban@example.com"
```

---

## 2. Clone repo và cài môi trường

```bash
git clone git@github.com:quang-ng/TradeMind.git
cd TradeMind

uv sync                    # cài Python dependencies
cp .env.example .env       # tạo file env local — KHÔNG commit file .env
```

Chạy test để chắc chắn môi trường ổn (test cần Postgres ở cổng 5432, giống CI):

```bash
docker run -d --name trademind-test-db -p 5432:5432 \
  -e POSTGRES_USER=trademind -e POSTGRES_PASSWORD=trademind -e POSTGRES_DB=trademind \
  postgres:16-alpine

uv run alembic upgrade head
make lint    # uv run ruff check .
make test    # uv run pytest
```

Nếu sửa frontend:

```bash
cd frontend && npm install
make frontend-lint frontend-test
```

---

## 3. Tạo branch

**Không bao giờ commit thẳng lên `main`.** Mỗi việc làm trên một branch riêng, tách ra từ `main` mới nhất:

```bash
git checkout main
git pull origin main
git checkout -b feat/ten-tinh-nang
```

Quy ước đặt tên branch:

| Prefix        | Dùng khi                          | Ví dụ                           |
|---------------|-----------------------------------|---------------------------------|
| `feat/`       | Thêm tính năng mới                | `feat/stoploss-on-exchange`     |
| `fix/`        | Sửa bug                           | `fix/signal-publish-race`       |
| `docs/`       | Chỉ sửa tài liệu                  | `docs/how-it-works`             |
| `experiment/` | Thử nghiệm, backtest              | `experiment/exit-tuning-backtest` |

Giữ branch **nhỏ, một mục đích** — nếu việc lớn quá, tách thành nhiều branch/PR.

---

## 4. Tạo commit

Xem những gì đã thay đổi:

```bash
git status
git diff
```

Add đúng file cần commit (tránh `git add .` để không lỡ add `.env` hay file rác):

```bash
git add services/risk_engine/app/rules/my_rule.py services/risk_engine/tests/test_my_rule.py
git commit -m "feat: add max-drawdown rule to risk engine"
```

Quy ước commit message (theo [Conventional Commits](https://www.conventionalcommits.org/)):

```
<type>: <mô tả ngắn, thể mệnh lệnh>

<(tuỳ chọn) giải thích TẠI SAO thay đổi, không chỉ LÀM GÌ>
```

- `type`: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`
- Mỗi commit một mục đích; không trộn refactor không liên quan vào commit tính năng.

Trước khi commit, luôn chạy:

```bash
make lint && make test
```

---

## 5. Push và tạo Pull Request

Push branch lên GitHub:

```bash
git push -u origin feat/ten-tinh-nang
```

Tạo PR — chọn một trong hai cách:

**Cách 1 — trên web:** mở https://github.com/quang-ng/TradeMind, GitHub sẽ hiện nút **"Compare & pull request"** → chọn base `main` → điền tiêu đề + mô tả → **Create pull request**.

**Cách 2 — bằng `gh`:**

```bash
gh auth login     # lần đầu
gh pr create --base main --title "feat: add max-drawdown rule" --body "..."
```

Mô tả PR nên có:

- **Mục đích:** vấn đề gì, tại sao cần sửa
- **Thay đổi chính:** file/module nào bị ảnh hưởng
- **Cách test:** lệnh đã chạy, kết quả
- **Rủi ro:** có đụng tới risk engine, sizing, execution, contract (API/Redis/env) không

Sau khi tạo PR:

1. CI (GitHub Actions) tự chạy lint + test — phải **xanh** mới được merge.
2. Chờ review. Nếu cần sửa, cứ commit tiếp trên cùng branch rồi `git push`, PR tự cập nhật.
3. Owner sẽ merge khi PR ổn. Sau đó xoá branch local:

```bash
git checkout main
git pull origin main
git branch -d feat/ten-tinh-nang
```

---

## 6. Cập nhật branch khi `main` có thay đổi mới

```bash
git checkout feat/ten-tinh-nang
git fetch origin
git rebase origin/main          # hoặc: git merge origin/main
# nếu có conflict: sửa file → git add <file> → git rebase --continue
git push --force-with-lease     # chỉ cần khi đã rebase
```

---

## 7. Những điều tuyệt đối không làm

- ❌ Commit file `.env`, API key, private key, hay bất kỳ secret nào.
- ❌ Đổi `DRY_RUN` hay bật live trading.
- ❌ Bỏ qua / tắt Risk Engine hoặc gọi Freqtrade `forceenter` từ chỗ khác ngoài `risk_engine`.
- ❌ Cho LLM truy cập Binance hoặc tính position size.
- ❌ Xoá hay làm yếu test để CI xanh.
- ❌ Chạy `docker compose up` trỏ vào môi trường production, hoặc deploy lên VPS — việc deploy do owner làm.

Chi tiết và lý do: xem `AGENTS.md` mục 4 và 7, `PROJECT.md` mục 14.

---

## Tóm tắt nhanh

```bash
git checkout main && git pull origin main
git checkout -b feat/viec-can-lam
# ... code + test ...
make lint && make test
git add <files>
git commit -m "feat: mô tả ngắn"
git push -u origin feat/viec-can-lam
gh pr create --base main
```
