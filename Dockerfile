FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir poetry==2.3.2

WORKDIR /app

COPY pyproject.toml poetry.lock ./

RUN poetry config virtualenvs.in-project true && \
    poetry install --no-interaction --no-ansi --only main --no-root

FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /app/.venv .venv
COPY src/ src/

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

ENTRYPOINT ["python", "-m", "src.main"]