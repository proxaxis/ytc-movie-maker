FROM node:20-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# Chromium ヘッドレスシェル依存 + ffmpeg（セグメント結合用）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    libnss3 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json ./
RUN npm install --no-audit --no-fund

# Remotion 用のヘッドレスブラウザをイメージビルド時に取得
RUN npx remotion browser ensure

COPY . .

EXPOSE 3100
CMD ["npm", "start"]
