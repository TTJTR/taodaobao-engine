FROM node:20-bookworm-slim AS upstream
ARG OPEN_ENRICH_REF=301af4b42131e8cf5aae406e07f1b2c0afff8313
RUN corepack enable && apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/open-enrich
RUN git clone https://github.com/brightdata/open-enrich.git . \
    && git checkout "$OPEN_ENRICH_REF"
RUN pnpm install --frozen-lockfile \
    && pnpm --filter @brightdata/enrich-core build \
    && cd packages/core && pnpm pack --pack-destination /tmp

FROM node:20-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json* tsconfig.json ./
RUN npm install
COPY src ./src
RUN npm run build && npm prune --omit=dev

FROM node:20-bookworm-slim AS runtime
ENV NODE_ENV=production PORT=8090 HOST=0.0.0.0
WORKDIR /app
COPY --from=build /app/package.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=upstream /tmp/brightdata-enrich-core-*.tgz /tmp/enrich-core.tgz
RUN npm install --omit=dev /tmp/enrich-core.tgz && rm /tmp/enrich-core.tgz
USER node
EXPOSE 8090
CMD ["node", "dist/server.js"]
