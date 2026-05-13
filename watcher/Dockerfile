FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY watcher/pyproject.toml ./pyproject.toml
COPY watcher/src ./src

RUN pip install --upgrade pip && pip install .

# Phase 4 ships the actual watcher loop — this scaffold idles forever so the
# compose stack has a slot to extend into. Override CMD in Phase 4 compose.
CMD ["python", "-c", "import time; time.sleep(2**31)"]
