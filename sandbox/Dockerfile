# Throwaway sandbox for the field study. It runs OTHER PEOPLE'S tests, so it never runs on the host:
# one container per pull request, no mounted source, no secrets, dropped capabilities, a read-only
# root filesystem and resource limits (set by `agenticqa field-study` at `docker run`).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git build-essential ca-certificates curl libffi-dev libssl-dev \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

RUN useradd -m -u 1000 runner && mkdir -p /out && chown runner /out
ARG GIT_SHA=unknown
USER runner
ENV HOME=/home/runner PYTHONUNBUFFERED=1 AGENTICQA_GIT_SHA=${GIT_SHA}
ENTRYPOINT ["python", "-m", "agenticqa.cli"]
