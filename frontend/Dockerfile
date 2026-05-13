# Multi-stage build: Node compiles the Vite SPA, nginx serves the static bundle.

FROM node:20-alpine AS builder
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginx:alpine AS runtime

# OCI image labels — link the published image back to source + license.
LABEL org.opencontainers.image.title="ursa-oscar-web" \
      org.opencontainers.image.description="URSA-OSCAR web UI (React + Vite + uPlot). Daily/Overview/Statistics/Events/Import/Settings pages." \
      org.opencontainers.image.source="https://github.com/burrellka/URSA-OSCAR" \
      org.opencontainers.image.url="https://github.com/burrellka/URSA-OSCAR" \
      org.opencontainers.image.documentation="https://github.com/burrellka/URSA-OSCAR/blob/main/README.md" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.vendor="URSA-OSCAR project"

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
