FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: pyedflib + mne build wheels via numpy; need libgomp for numpy SIMD
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./pyproject.toml
COPY backend/src ./src

# Editable install so the dev compose's /app/src bind mount immediately
# reflects host edits into the running uvicorn process. Production builds
# don't bind-mount anything, so editable just means the package metadata
# points at /app/src — same files, same behavior.
RUN pip install --upgrade pip && pip install -e .

# Runtime
ENV URSA_OSCAR_DB_PATH=/data/ursa-oscar.duckdb
EXPOSE 8000
CMD ["uvicorn", "ursa_oscar.main:app", "--host", "0.0.0.0", "--port", "8000"]
