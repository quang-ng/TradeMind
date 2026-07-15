FROM python:3.12-slim

WORKDIR /srv

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/common ./common
# `POST /cycles/{symbol}/trigger` (PROJECT.md Section 11) reuses the
# Scheduler's own `run_cycle` rather than duplicating cycle logic here —
# only the package itself, not services/scheduler/tests.
COPY services/scheduler/__init__.py ./scheduler/__init__.py
COPY services/scheduler/app ./scheduler/app
COPY services/admin_api/app ./app

ENV PATH="/srv/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
