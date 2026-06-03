# yonnn API (FastAPI) — Python 3.11
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Subfinder (ProjectDiscovery) — required by workers.tasks.discovery; not on PATH in slim images.
# Bump SUBFINDER_VERSION when you want a newer release: https://github.com/projectdiscovery/subfinder/releases
ARG SUBFINDER_VERSION=2.6.7
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
      x86_64) sf_arch=amd64 ;; \
      aarch64) sf_arch=arm64 ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/subfinder.zip \
      "https://github.com/projectdiscovery/subfinder/releases/download/v${SUBFINDER_VERSION}/subfinder_${SUBFINDER_VERSION}_linux_${sf_arch}.zip"; \
    unzip -o /tmp/subfinder.zip -d /tmp/subfinder-extract; \
    bin="$(find /tmp/subfinder-extract -type f -name subfinder | head -1)"; \
    test -n "$bin" -a -f "$bin"; \
    install -m 755 "$bin" /usr/local/bin/subfinder; \
    rm -rf /tmp/subfinder.zip /tmp/subfinder-extract; \
    subfinder -version

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Windows CRLF breaks shebang/exec ("no such file or directory"); normalize line endings.
RUN sed -i 's/\r$//' /app/scripts/docker-entrypoint.sh \
    && chmod +x /app/scripts/docker-entrypoint.sh \
    && groupadd --system yonnn \
    && useradd --system --gid yonnn --home-dir /app --shell /usr/sbin/nologin yonnn \
    && chown -R yonnn:yonnn /app

USER yonnn

EXPOSE 8000

# Invoke via sh so a bad shebang/CRLF cannot break container start.
ENTRYPOINT ["/bin/sh", "/app/scripts/docker-entrypoint.sh"]
CMD ["api"]
