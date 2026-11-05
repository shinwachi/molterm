# molterm-3d: Mol* in Node, drawing in software OpenGL (Mesa's llvmpipe) on a virtual X display, for terminals and a web page.
# Built from the repository's root, to share the house stylesheet: docker build -f molterm-3d/Dockerfile .
FROM node:22-bookworm-slim AS build
# headless-gl has no prebuilt binary for this Node, so it's compiled here
RUN apt-get update && apt-get install -y --no-install-recommends python3 python-is-python3 make g++ pkg-config \
        libxi-dev libglu1-mesa-dev libglew-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY molterm-3d/package.json molterm-3d/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force

FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl xvfb xauth libgl1-mesa-dri libglx-mesa0 libxi6 libglew2.2 \
    && rm -rf /var/lib/apt/lists/*
ENV NODE_ENV=production HOME=/tmp
WORKDIR /app
COPY --from=build /app/node_modules node_modules
COPY server/static/house.css vendor/house.css
COPY molterm-3d/ .

EXPOSE 8930
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fs http://localhost:8930/health || exit 1
# xvfb-run gives headless-gl a display; -a picks a free one
CMD ["xvfb-run", "-a", "-s", "-screen 0 1280x1024x24", "node", "src/server.js"]
