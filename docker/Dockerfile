FROM debian:bookworm-slim

ARG S6_VERSION=3.2.0.2
ARG NAVIDROME_VERSION=0.54.5
ARG TARGETARCH=amd64

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils unzip ffmpeg python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# s6-overlay (process supervisor). x86_64 build; change for arm64 hosts.
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_VERSION}/s6-overlay-noarch.tar.xz /tmp/
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_VERSION}/s6-overlay-x86_64.tar.xz /tmp/
RUN tar -C / -Jxpf /tmp/s6-overlay-noarch.tar.xz \
 && tar -C / -Jxpf /tmp/s6-overlay-x86_64.tar.xz \
 && rm /tmp/*.tar.xz

# Navidrome (unmodified release binary)
RUN curl -fL https://github.com/navidrome/navidrome/releases/download/v${NAVIDROME_VERSION}/navidrome_${NAVIDROME_VERSION}_linux_${TARGETARCH}.tar.gz \
    | tar -xz -C /usr/local/bin navidrome

# yt-dlp standalone binary (updated at runtime by the app)
RUN curl -fL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
    -o /usr/local/bin/yt-dlp && chmod +x /usr/local/bin/yt-dlp

# Deno: the JavaScript runtime yt-dlp needs for YouTube (x86_64 build; use aarch64-unknown-linux-gnu on arm64)
RUN curl -fL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip \
 && unzip -q /tmp/deno.zip -d /usr/local/bin && chmod 755 /usr/local/bin/deno && rm /tmp/deno.zip

COPY app/ /opt/app/
RUN pip3 install --break-system-packages --no-cache-dir -r /opt/app/requirements.txt

COPY docker/rootfs/ /

ENV ND_MUSICFOLDER=/music \
    ND_DATAFOLDER=/data \
    ND_ADDRESS=127.0.0.1 \
    ND_PORT=4533 \
    MUSIC_DIR=/music \
    NAVIDROME_URL=http://127.0.0.1:4533 \
    S6_KEEP_ENV=1

EXPOSE 8080
VOLUME ["/music", "/data"]
ENTRYPOINT ["/init"]
