FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# yt-dlp needs a JavaScript runtime for YouTube downloads.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY pyproject.toml README.md ./
COPY clipper ./clipper
RUN pip install --no-cache-dir .

ENV WORK_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
CMD ["clipper", "worker"]
