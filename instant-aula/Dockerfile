FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl cron jq \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml uv.lock* /app/
COPY src /app/src
COPY scripts /app/scripts
RUN uv sync --no-dev

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
