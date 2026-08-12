FROM python:3.13.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --create-home app

COPY pyproject.toml ./
COPY src/github-broker ./src/github-broker

RUN python -m pip install --no-cache-dir --upgrade pip==26.1.1 \
    && python -m pip install --no-cache-dir .

USER app
EXPOSE 8000

CMD ["sh", "-c", "uvicorn github_broker.main:app --host 0.0.0.0 --port ${PORT}"]
