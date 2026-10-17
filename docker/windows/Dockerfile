# Cross-compile 86Box (with the automation socket) for 64-bit Windows.
# Fedora packages a MinGW toolchain with Qt6 and most dependencies; libslirp
# and libsndfile are not packaged, so they are built from source here.
FROM fedora:42

RUN dnf -y install --setopt=install_weak_deps=False \
      mingw64-gcc mingw64-gcc-c++ mingw64-qt6-qtbase mingw64-qt6-qttools mingw64-SDL3 \
      mingw64-libpng mingw64-freetype mingw64-openal-soft mingw64-glib2 mingw64-zstd mingw64-zlib \
      qt6-qtbase-devel qt6-qttools-devel qt6-linguist \
      cmake ninja-build meson git pkgconf-pkg-config wget xz python3 \
 && dnf clean all

ENV SYSROOT=/usr/x86_64-w64-mingw32/sys-root/mingw

# libslirp (user-mode networking), via meson with Fedora's MinGW cross file.
RUN git clone --depth 1 -b v4.9.1 https://gitlab.freedesktop.org/slirp/libslirp.git /tmp/slirp \
 && cd /tmp/slirp \
 && meson setup build --cross-file /usr/share/mingw/toolchain-mingw64.meson --prefix $SYSROOT \
      --default-library shared \
 && ninja -C build install && rm -rf /tmp/slirp

# libsndfile, without external codecs (86Box only uses it for audio CD images).
# Static: 86Box's Windows build links it with -static (as MSYS2 provides), which
# also makes the libraries after it, like iconv, static.
RUN git clone --depth 1 -b 1.2.2 https://github.com/libsndfile/libsndfile.git /tmp/sndfile \
 && cd /tmp/sndfile \
 && mingw64-cmake -G Ninja -B build -D BUILD_SHARED_LIBS=OFF -D BUILD_PROGRAMS=OFF -D BUILD_EXAMPLES=OFF \
      -D BUILD_TESTING=OFF -D ENABLE_EXTERNAL_LIBS=OFF -D ENABLE_MPEG=OFF -D INSTALL_MANPAGES=OFF \
 && cmake --build build && cmake --install build && rm -rf /tmp/sndfile

# Fedora's MinGW zlib CMake config references the static library too.
RUN dnf -y install --setopt=install_weak_deps=False mingw64-zlib-static mingw64-win-iconv-static && dnf clean all

# Case shims: upstream includes <Windows.h> and links -lSetupAPI, which only
# resolve on case-insensitive Windows; MinGW on Linux ships lowercase names.
COPY Windows.h $SYSROOT/include/Windows.h
RUN ln -s libsetupapi.a $SYSROOT/lib/libSetupAPI.a

WORKDIR /src
