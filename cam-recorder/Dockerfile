ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG GO2RTC_VERSION

RUN apk add --no-cache python3 py3-aiohttp ffmpeg rclone curl

RUN case "${BUILD_ARCH}" in \
      amd64) arch=amd64 ;; \
      aarch64) arch=arm64 ;; \
      *) echo "unsupported arch ${BUILD_ARCH}" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/go2rtc \
      "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/go2rtc_linux_${arch}" \
 && chmod +x /usr/local/bin/go2rtc \
 && go2rtc -version

COPY app /app
WORKDIR /app
ENV PYTHONUNBUFFERED=1

# with-contenv: s6 does not hand the container env (SUPERVISOR_TOKEN) to CMD
CMD ["/usr/bin/with-contenv", "python3", "-m", "cam_recorder"]
