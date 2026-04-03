FROM node:20-bookworm-slim

ARG BACKEND_PROXY_URL=http://stock-ticker-backend:8000

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    BACKEND_PROXY_URL=${BACKEND_PROXY_URL}

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./

RUN npm ci

COPY frontend/. .
COPY packages/shared-contracts /app/packages/shared-contracts

RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
