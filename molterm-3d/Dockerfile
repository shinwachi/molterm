# molterm-3d: Mol* in Node, drawing in software OpenGL (Mesa's llvmpipe) on a virtual X display, for terminals and a web page.
# Built from the repository's root, to share the house stylesheet: docker build -f molterm-3d/Dockerfile .
FROM node:22-bookworm-slim AS build
# headless-gl has no prebuilt binary for this Node, so it's compiled here
RUN apt-get update && apt-get install -y --no-install-recommends python3 python-is-python3 make g++ pkg-config \
        libxi-dev libglu1-mesa-dev libglew-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY molterm-3d/package.json molterm-3d/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
# restty, the showcase's web terminal with Kitty graphics, and a font for it, since its default fonts come from a CDN: the same
# versions as molterm's, checked against the same sums
RUN mkdir -p /app/vendor && cd /tmp && set -e; \
    curl -fsSL -o restty.tgz https://registry.npmjs.org/restty/-/restty-0.3.0.tgz; \
    echo "7ffffb6b4d04e8344dd120effe93d081740b733e642e5f2bdae829eb3da16f37  restty.tgz" | sha256sum -c -; \
    tar -xzf restty.tgz; \
    cp package/dist/restty.esm.js /app/vendor/; \
    cp package/LICENSE /app/vendor/LICENSE.restty; \
    rm -rf package restty.tgz; \
    curl -fsSL -o dejavu.tgz https://registry.npmjs.org/dejavu-fonts-ttf/-/dejavu-fonts-ttf-2.37.3.tgz; \
    echo "b7013ac58f9225250619db82cfb72e99c37ca8195f2b5a06a9e6a95bc5e1c84a  dejavu.tgz" | sha256sum -c -; \
    tar -xzf dejavu.tgz; \
    cp package/ttf/DejaVuSansMono.ttf package/ttf/DejaVuSansMono-Bold.ttf /app/vendor/; \
    cp package/LICENSE /app/vendor/LICENSE.dejavu; \
    rm -rf package dejavu.tgz

FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl xvfb xauth libgl1-mesa-dri libglx-mesa0 libxi6 libglew2.2 \
    && rm -rf /var/lib/apt/lists/*
ENV NODE_ENV=production HOME=/tmp
WORKDIR /app
COPY --from=build /app/node_modules node_modules
COPY --from=build /app/vendor vendor
COPY server/static/house.css vendor/house.css
COPY molterm-3d/ .

EXPOSE 8930
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fs http://localhost:8930/health || exit 1
# xvfb-run gives headless-gl a display; -a picks a free one
CMD ["xvfb-run", "-a", "-s", "-screen 0 1280x1024x24", "node", "src/server.js"]
