# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS frontend
WORKDIR /web
COPY web/package*.json ./
RUN npm ci --ignore-scripts
COPY web/ ./
RUN npm run build

FROM rust:1.95-bookworm AS build
ARG STUDIO_REF=61064d27fcb4e7a7a231b9f6aa546e59bee00084
ARG NRSC5_REF=a5c09723b135327aeef157770caae9aebf597547
ARG MULTIMON_REF=b5f7d71c5e9b5a76093ba55c515968dc40f92804
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake build-essential pkg-config clang libclang-dev autoconf automake libtool \
    patch git python3 ca-certificates libfftw3-dev librtlsdr-dev libusb-1.0-0-dev \
    libsoapysdr-dev libasound2-dev libopus-dev libudev-dev libdbus-1-dev \
    libwayland-dev libxkbcommon-dev libx11-dev libxrandr-dev libxi-dev \
    libxcursor-dev libxinerama-dev libgl1-mesa-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init multimon-ng && cd multimon-ng \
    && git remote add origin https://github.com/EliasOenal/multimon-ng.git \
    && git fetch --depth 1 origin "$MULTIMON_REF" && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$MULTIMON_REF" \
    && cmake -S . -B /build/multimon -DCMAKE_BUILD_TYPE=Release \
       -DX11_SUPPORT=OFF -DPULSE_AUDIO_SUPPORT=OFF -DSDL3_SCOPE=OFF -DBUILD_GEN_NG=OFF -DBUILD_TESTING=OFF \
    && cmake --build /build/multimon --target multimon-ng -j 2
COPY scripts/test-same-decoder.py /build/test-same-decoder.py
RUN python3 /build/test-same-decoder.py /build/multimon/multimon-ng
RUN git init studio && cd studio \
    && git remote add origin https://github.com/LTCAshraven/nrsc5-studio.git \
    && git fetch --depth 1 origin "$STUDIO_REF" && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$STUDIO_REF"
RUN git init nrsc5 && cd nrsc5 \
    && git remote add origin https://github.com/theori-io/nrsc5.git \
    && git fetch --depth 1 origin "$NRSC5_REF" && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$NRSC5_REF"
# Use distro FFTW/USB/RTL-SDR: upstream's bundled FFTW enables x86 AVX.
# Keep the upstream HDC-patched FAAD2 build (ordinary distro FAAD cannot decode HDC).
# Scalar nrsc5 is portable; do not use the upstream ARM32 NEON compiler flags.
COPY scripts/patch-nrsc5.py /build/patch-nrsc5.py
COPY backend/constellation.c /build/backend/constellation.c
RUN python3 /build/patch-nrsc5.py /src/nrsc5 /build/backend
RUN cmake -S nrsc5 -B /build/nrsc5 -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_CLI=OFF -DUSE_STATIC=OFF -DUSE_SSE=OFF -DUSE_NEON=OFF \
    -DUSE_SYSTEM_FFTW=ON -DUSE_SYSTEM_RTLSDR=ON -DUSE_SYSTEM_LIBUSB=ON \
    -DUSE_FAAD2=ON -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    && cmake --build /build/nrsc5 --target nrsc5 -j 2 \
    && cp /build/nrsc5/src/libnrsc5.so /src/studio/bin/libnrsc5.so
COPY scripts/test-constellation.c /build/test-constellation.c
RUN cc -std=c11 -Wall -Wextra -Werror -I/src/nrsc5/include -I/src/nrsc5/src \
    -I/build/nrsc5/src -I/build/backend /build/test-constellation.c -lm -o /build/test-constellation \
    && /build/test-constellation
COPY scripts/patch-studio.py /build/patch-studio.py
COPY backend/ /build/backend/
RUN python3 /build/patch-studio.py /src/studio /build/backend
WORKDIR /src/studio
# Explicit toolchain bypasses upstream's floating rust-toolchain.toml.
# Avoid -C target-cpu=native: CI's ARM CPU need not match the ROSE CPU.
RUN cargo +1.95.0 build --locked --release --bin nrsc5-studio --features eframe/glow
RUN LD_LIBRARY_PATH=/src/studio/bin cargo +1.95.0 test --locked --release --bin nrsc5-studio --features eframe/glow
# Retain corresponding source, patched FAAD2, Cargo.lock and Rust dependencies.
RUN mkdir -p /sources/studio/.cargo /sources/nrsc5 /sources/faad2 \
    && cargo +1.95.0 vendor --locked /sources/studio/vendor > /sources/studio/.cargo/config.toml \
    && tar --exclude=.git --exclude=target --exclude=bin --exclude=.cargo \
       -cf - . | tar -xf - -C /sources/studio \
    && cp -a /src/nrsc5/. /sources/nrsc5/ \
    && cp -a /src/multimon-ng /sources/multimon-ng && rm -rf /sources/multimon-ng/.git \
    && cp -a /build/nrsc5/faad2-prefix/src/faad2_external/. /sources/faad2/ \
    && rm -rf /sources/nrsc5/.git /sources/faad2/.git \
    && printf 'STUDIO_REF=%s\nNRSC5_REF=%s\nMULTIMON_REF=%s\n' "$STUDIO_REF" "$NRSC5_REF" "$MULTIMON_REF" > /sources/versions.txt
COPY Dockerfile README.md LICENSE compose.yaml /sources/hd-radio-rose/
COPY container/ /sources/hd-radio-rose/container/
COPY scripts/ /sources/hd-radio-rose/scripts/
COPY web/ /sources/hd-radio-rose/web/
COPY backend/ /sources/hd-radio-rose/backend/
COPY tests/ /sources/hd-radio-rose/tests/
COPY .github/ /sources/hd-radio-rose/.github/
RUN tar -czf /sources.tar.gz -C /sources .

FROM scratch AS source-export
COPY --from=build /sources/ /

FROM debian:bookworm-slim AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 nginx-light openssl \
    ffmpeg hdhomerun-config libasound2 libdbus-1-3 \
    libxkbcommon0 libwayland-client0 libx11-6 libx11-xcb1 libxcb1 \
    libsoapysdr0.8 soapysdr-tools soapysdr0.8-module-rtlsdr \
    soapysdr0.8-module-hackrf soapysdr0.8-module-remote \
    rtl-sdr hackrf usbutils libfftw3-single3 libopus0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/studio/target/release/nrsc5-studio /opt/nrsc5-studio/nrsc5-studio
COPY --from=build /build/multimon/multimon-ng /usr/local/bin/multimon-ng
COPY --from=build /src/studio/bin/libnrsc5.so /usr/lib/nrsc5-studio/libnrsc5.so
COPY --from=build /src/studio/res/map.png /usr/share/nrsc5-studio/map.png
COPY --from=build /src/studio/LICENSE /src/studio/COPYING.GPL-3.0 /src/studio/THIRD_PARTY_NOTICES.md /usr/share/doc/nrsc5-studio/
COPY container/ /opt/rose/
COPY --from=frontend /web/dist/ /var/www/rose/
COPY README.md LICENSE /usr/share/doc/hd-radio-rose/
COPY --from=build /sources.tar.gz /usr/share/doc/hd-radio-rose/sources.tar.gz
RUN touch /opt/nrsc5-studio/portable.txt \
    && ln -s /data /opt/nrsc5-studio/data \
    && mkdir -p /data /run/rose \
    && cp /opt/rose/nginx.conf /etc/nginx/nginx.conf
RUN ldd /opt/nrsc5-studio/nrsc5-studio > /tmp/linked-libraries \
    && cat /tmp/linked-libraries && ! grep -q 'not found' /tmp/linked-libraries
ENV WEB_USER=radio PYTHONPATH=/opt/rose
EXPOSE 8080
WORKDIR /data
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python3", "/opt/rose/healthcheck.py"]
ENTRYPOINT ["python3", "/opt/rose/entrypoint.py"]
