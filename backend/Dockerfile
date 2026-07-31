FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
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
