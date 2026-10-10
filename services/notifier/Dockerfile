FROM python:3.12-slim

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/common ./common
COPY services/notifier/app ./app

ENV PATH="/srv/.venv/bin:$PATH"

CMD ["python", "-m", "app.main"]
