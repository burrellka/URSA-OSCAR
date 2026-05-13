FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY mcp-server/pyproject.toml ./pyproject.toml
COPY mcp-server/src ./src

RUN pip install --upgrade pip && pip install .

# Runtime — same /data volume as the API container; read-only access.
ENV URSA_OSCAR_DB_PATH=/data/ursa-oscar.duckdb
EXPOSE 8000
CMD ["python", "-m", "ursa_oscar_mcp"]
