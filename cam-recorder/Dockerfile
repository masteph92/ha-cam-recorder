ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG GO2RTC_VERSION
ARG HLSJS_VERSION=1.5.20

RUN apk add --no-cache python3 py3-aiohttp ffmpeg rclone curl

RUN case "${BUILD_ARCH}" in \
      amd64) arch=amd64 ;; \
      aarch64) arch=arm64 ;; \
      *) echo "unsupported arch ${BUILD_ARCH}" >&2; exit 1 ;; \
    esac \
 && curl -fsSL --retry 5 --retry-all-errors --retry-delay 3 -o /usr/local/bin/go2rtc \
      "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/go2rtc_linux_${arch}" \
 && chmod +x /usr/local/bin/go2rtc \
 && go2rtc -version

COPY app /app
# hls.js für die Wiedergabe in Chrome/Fire-Tablet, lokal statt CDN
RUN curl -fsSL --retry 5 --retry-all-errors --retry-delay 3 -o /app/cam_recorder/web/hls.min.js \
      "https://cdn.jsdelivr.net/npm/hls.js@${HLSJS_VERSION}/dist/hls.min.js"
WORKDIR /app
ENV PYTHONUNBUFFERED=1

# with-contenv: s6 does not hand the container env (SUPERVISOR_TOKEN) to CMD
CMD ["/usr/bin/with-contenv", "python3", "-m", "cam_recorder"]
