FROM python:3.13.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLMOPS_DATA_ROOT=/app/data \
    PORT=8000

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --create-home app

COPY pyproject.toml ./
COPY src/policy-agent ./src/policy-agent
COPY src/web ./src/web
COPY data ./data

RUN python -m pip install --no-cache-dir --upgrade pip==26.1.1 \
    && python -m pip install --no-cache-dir . \
    && python -c "from pathlib import Path; import webapp; assert (Path(webapp.__file__).parent / 'static' / 'index.html').is_file()"

USER app
EXPOSE 8000

CMD ["sh", "-c", "uvicorn webapp.main:app --host 0.0.0.0 --port ${PORT}"]
