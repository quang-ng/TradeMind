FROM python:3.12-slim

WORKDIR /srv

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY services/common ./common

ENV PATH="/srv/.venv/bin:$PATH" \
    PYTHONPATH="/srv"

CMD ["alembic", "upgrade", "head"]
