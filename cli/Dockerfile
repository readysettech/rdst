# RDST server image: the always-on monitoring agent and the web UI in one process.
#
# Build from the rdst/ directory once the web UI is in web_dist/
# (deploy/README.md has the steps):
#   docker build -t rdst .
# Run:
#   docker run -d --name rdst --hostname rdst --restart unless-stopped \
#     -p 8787:8787 -v rdst-data:/data readysettech/rdst

ARG PYTHON_VERSION=3.12
# The official Python image from AWS's public mirror of Docker Hub's library;
# CI points this at Readyset's own mirror.
ARG PUBLIC_REPO_BASE=public.ecr.aws

FROM ${PUBLIC_REPO_BASE}/docker/library/python:${PYTHON_VERSION}-slim-trixie AS build
# Precompiled bytecode makes startup faster. Set to 0 for emulated cross-builds,
# where uv's parallel compile can stall under QEMU.
ARG UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=${UV_COMPILE_BYTECODE} UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/rdst
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN test -f web_dist/index.html \
      || { echo "web_dist/index.html is missing: build the web UI first" >&2; exit 1; } \
    && uv sync --frozen --no-dev --no-editable

FROM ${PUBLIC_REPO_BASE}/docker/library/python:${PYTHON_VERSION}-slim-trixie
ARG RDST_VERSION=dev
LABEL org.opencontainers.image.title="RDST" \
      org.opencontainers.image.description="Readyset Data and SQL Toolkit: always-on query monitoring with a web UI" \
      org.opencontainers.image.source="https://github.com/readysettech/rdst" \
      org.opencontainers.image.url="https://readyset.io" \
      org.opencontainers.image.vendor="Readyset" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${RDST_VERSION}"
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates openssh-client \
    && apt-get clean \
    && groupadd --gid 10001 rdst \
    && useradd --uid 10001 --gid 10001 --home-dir /data --no-create-home \
         --shell /usr/sbin/nologin rdst \
    && mkdir -p /data \
    && chown 10001:10001 /data
COPY --from=build /opt/rdst /opt/rdst
ENV PATH=/opt/rdst/bin:$PATH \
    HOME=/data \
    RDST_DATA_DIR=/data/.rdst \
    RDST_SERVER=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=4).status != 200)"]
ENTRYPOINT ["tini", "--", "rdst"]
CMD ["web", "--host", "0.0.0.0", "--port", "8787", "--no-browser", "--ui", "dist"]
