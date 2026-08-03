FROM python:3.12-slim

WORKDIR /app

# Debian's official CDN is frequently slow or unreachable from mainland-China
# hosts. Keep the mirror configurable, but default production builds to Aliyun
# and fail within a bounded time instead of hanging indefinitely.
ARG DEBIAN_MIRROR=https://mirrors.aliyun.com
RUN sed -i \
      -e "s|http://deb.debian.org/debian-security|${DEBIAN_MIRROR}/debian-security|g" \
      -e "s|https://deb.debian.org/debian-security|${DEBIAN_MIRROR}/debian-security|g" \
      -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}/debian|g" \
      -e "s|https://deb.debian.org/debian|${DEBIAN_MIRROR}/debian|g" \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get \
      -o Acquire::Retries=5 \
      -o Acquire::http::Timeout=30 \
      -o Acquire::https::Timeout=30 \
      update \
    && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libxcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
# CN-friendly PyPI + ensure setuptools before packages that build from sdist (e.g. antlr4)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
    -U pip setuptools wheel \
 && pip install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
    -r /app/backend/requirements.txt

COPY backend /app/backend
COPY index.html login.html app.html auth.js app.js styles.css /app/

ENV PYTHONPATH=/app/backend
ENV PDF_STORAGE_DIR=/app/data/pdfs
ENV HOST=0.0.0.0
ENV PORT=8787
ENV JWT_REQUIRE_STRONG=1
ENV INDEX_RECOVER_ON_STARTUP=1

EXPOSE 8787

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
