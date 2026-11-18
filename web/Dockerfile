# syntax=docker/dockerfile:1
FROM node:22.13-bookworm-slim AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_API_URL
ARG VITE_BASE=/
ENV VITE_API_URL=$VITE_API_URL VITE_BASE=$VITE_BASE
RUN npx tsc -b && npx vite build

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
