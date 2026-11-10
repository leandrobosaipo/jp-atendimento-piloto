FROM node:22.23.2-alpine@sha256:b6f26b36c8ff49624cfdac716b8ea1138d606df02586a77d364bb5536a634f85
RUN apk add --no-cache chromium font-noto
WORKDIR /opt/renewal
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit && npm cache clean --force
COPY worker.mjs guard.mjs login.mjs ./
ENV HOME=/tmp LANG=C.UTF-8
USER 65532:65532
ENTRYPOINT ["node","worker.mjs"]
