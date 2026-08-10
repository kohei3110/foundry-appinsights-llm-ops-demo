FROM python:3.13.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8088 \
    LLMOPS_DATA_ROOT=/app/data

WORKDIR /app

RUN groupadd --system agent \
    && useradd --system --gid agent --create-home agent

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip==26.1.1 \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY policy_agent ./policy_agent
COPY data ./data
COPY entrypoint.sh /usr/local/bin/policy-agent-entrypoint

RUN chmod 0755 /usr/local/bin/policy-agent-entrypoint

EXPOSE 8088

ENTRYPOINT ["policy-agent-entrypoint"]
CMD ["python", "main.py"]
