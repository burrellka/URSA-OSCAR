FROM python:3.11-slim

# OCI image labels — link the published image back to source + license.
LABEL org.opencontainers.image.title="ursa-oscar-watcher" \
      org.opencontainers.image.description="URSA-OSCAR file-watcher daemon (Phase 4 scaffold). Idles until the watcher loop ships." \
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

# Phase 4 ships the actual watcher loop — this scaffold idles forever so the
# compose stack has a slot to extend into. Override CMD in Phase 4 compose.
CMD ["python", "-c", "import time; time.sleep(2**31)"]
