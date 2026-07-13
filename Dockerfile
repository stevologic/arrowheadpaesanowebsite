FROM node:20-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY hugo.yaml ./
COPY content ./content
COPY data ./data
COPY layouts ./layouts
COPY public ./public
RUN npm run build

FROM node:20-alpine AS runtime

WORKDIR /app

COPY package*.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY server.js ./server.js
COPY lib ./lib
COPY data ./data
COPY --from=build /app/dist ./dist

ENV NODE_ENV=production
ENV PORT=1515

RUN chown -R node:node /app
USER node

EXPOSE 1515

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -qO- http://localhost:1515/healthz >/dev/null || exit 1

CMD ["node", "server.js"]
