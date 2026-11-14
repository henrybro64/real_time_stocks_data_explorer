ARG NODE_BASE_IMAGE=node:20-bookworm-slim
FROM ${NODE_BASE_IMAGE}

ARG BACKEND_PROXY_URL=http://stock-ticker-backend:8000
# Baked at image build time — Next inlines NEXT_PUBLIC_* during `next build` (#357).
# Runtime ConfigMap alone cannot change these values.
ARG BUILD_SHA=unknown
ARG BUILD_TIME=unknown

ENV NEXT_TELEMETRY_DISABLED=1 \
    BACKEND_PROXY_URL=${BACKEND_PROXY_URL} \
    NEXT_PUBLIC_BUILD_SHA=${BUILD_SHA} \
    NEXT_PUBLIC_BUILD_TIME=${BUILD_TIME}

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./

# Keep NODE_ENV unset during install so TypeScript/types are available for `next build`.
# Production NODE_ENV is set after the build (and after pruning devDependencies).
RUN npm ci

COPY frontend/. .
COPY packages/shared-contracts /app/packages/shared-contracts

RUN npm run build \
    && npm prune --omit=dev

ENV NODE_ENV=production

EXPOSE 3000

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
