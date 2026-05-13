FROM python:3.11-slim AS base

# OCI image labels — link the published image back to source + license.
LABEL org.opencontainers.image.title="ursa-oscar-mcp" \
      org.opencontainers.image.description="URSA-OSCAR MCP server (FastMCP + SSE, OAuth 2.1). Exposes nightly analytics tools for AI assistants." \
      org.opencontainers.image.source="https://github.com/burrellka/URSA-OSCAR" \
      org.opencontainers.image.url="https://github.com/burrellka/URSA-OSCAR" \
      org.opencontainers.image.documentation="https://github.com/burrellka/URSA-OSCAR/blob/main/Docs/03-mcp-tool-contract.md" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.vendor="URSA-OSCAR project"

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
