FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_DIR=/config \
    DATA_DIR=/data

# nsenter (util-linux) reaches the host; nfs-common/cifs-utils/fuse3/rclone cover in-container mounts and tests.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      util-linux nfs-common cifs-utils fuse3 rclone tini ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests ./tests
COPY pytest.ini .

RUN mkdir -p /config /data /mnt

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/healthz >/dev/null || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
