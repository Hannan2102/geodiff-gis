# syntax=docker/dockerfile:1
# Build context is the repository root so the build can copy versioned fixtures.
FROM node:22.13-bookworm-slim AS build
WORKDIR /repo/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY fixtures /repo/fixtures
COPY web/ ./
ARG VITE_API_URL
ARG VITE_BASE=/
ENV VITE_API_URL=$VITE_API_URL VITE_BASE=$VITE_BASE
RUN npm run build

FROM nginx:1.29-alpine
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/web/dist /usr/share/nginx/html
