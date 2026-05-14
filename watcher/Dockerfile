FROM python:3.11-slim

# OCI image labels — link the published image back to source + license.
LABEL org.opencontainers.image.title="ursa-oscar-watcher" \
      org.opencontainers.image.description="URSA-OSCAR file-watcher daemon. Detects new ResMed DATALOG/ data and enqueues an async import job via the API." \
      org.opencontainers.image.source="https://github.com/burrellka/URSA-OSCAR" \
      org.opencontainers.image.url="https://github.com/burrellka/URSA-OSCAR" \
      org.opencontainers.image.documentation="https://github.com/burrellka/URSA-OSCAR/blob/main/README.md" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.vendor="URSA-OSCAR project"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY watcher/pyproject.toml ./pyproject.toml
COPY watcher/src ./src

RUN pip install --upgrade pip && pip install .

# Phase 4 Ticket 3 — the real watcher loop. Polls ${URSA_OSCAR_WATCH_PATH}
# (default /cpap-import), waits for filesystem quiescence, then POSTs to
# the API's async /imports endpoint. See README + the config module for
# all env-var knobs.
CMD ["python", "-m", "ursa_oscar_watcher"]
