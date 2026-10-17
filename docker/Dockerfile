# Build and runtime environment for 86Box with the automation socket.
# 86Box is built with plain CMake against Debian's libraries and run inside
# this image, so the host needs nothing but Docker.
FROM debian:trixie

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential cmake ninja-build pkg-config git ca-certificates \
      qt6-base-dev qt6-base-private-dev qt6-tools-dev qt6-tools-dev-tools qt6-l10n-tools \
      libqt6opengl6-dev libvulkan-dev libgl-dev \
      libsdl3-dev libpng-dev libfreetype-dev libopenal-dev librtmidi-dev libfluidsynth-dev \
      libsndfile1-dev libslirp-dev libserialport-dev libevdev-dev libxkbcommon-x11-dev \
      libx11-dev libwayland-dev libzstd-dev libedit-dev \
      qt6-qpa-plugins libxcb-cursor0 \
      mtools tini \
 && rm -rf /var/lib/apt/lists/*

ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} box && useradd -m -u ${UID} -g ${GID} box
USER box
ENV QT_QPA_PLATFORM=offscreen
