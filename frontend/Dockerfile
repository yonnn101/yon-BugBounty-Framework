# syntax=docker/dockerfile:1
FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
# DevDependencies (@types/node, typescript, vite, …) required for `tsc` + `vite build`.
RUN npm ci

COPY . .
# Same-origin API under /api (see main.py + nginx.conf); SPA keeps /programs for React Router.
ENV VITE_API_BASE_URL=/api
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
