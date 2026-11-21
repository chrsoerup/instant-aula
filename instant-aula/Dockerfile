FROM debian:bookworm-slim

ENV TZ=Europe/Copenhagen
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl jq tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
# uv caches in /root but the venv lives in /app (different filesystems in the
# add-on), so hardlinks always fail and uv warns on every run. Copy instead.
ENV UV_LINK_MODE=copy
# Python buffers stdout when it is a pipe, so a job's output reached the app Log only when
# the job ended -- a calendar run that takes a couple of minutes looked frozen. Unbuffered
# means each line shows up as it is printed.
ENV PYTHONUNBUFFERED=1
# The image is built with `uv sync --no-dev`; without this, every container start made
# `uv run` fetch the dev group (pytest and friends) again, slowing startup and needing network.
ENV UV_NO_DEV=1

WORKDIR /app
COPY pyproject.toml uv.lock* /app/
COPY src /app/src
COPY scripts /app/scripts
RUN uv sync --no-dev

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
