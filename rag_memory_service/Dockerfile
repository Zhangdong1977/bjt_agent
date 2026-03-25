# rag-memory HTTP Service Dockerfile
# 使用 Debian 基础镜像以支持 better-sqlite3 预编译二进制

# 声明代理参数（在 docker-compose.yml 中传递）
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

# Build stage - 使用官方镜像（通过代理拉取）
FROM node:18-bullseye-slim AS builder

# 设置代理环境变量
ENV http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    no_proxy=${NO_PROXY}

# Set working directory
WORKDIR /app

# Copy package files
COPY rag_memory_service/package.json ./
COPY rag_memory_service/tsconfig.json ./

# Install all dependencies (better-sqlite3 会自动下载预编译的 Debian 二进制)
RUN npm install

# Copy source code and rag-memory types
COPY rag_memory_service/src ./src
COPY rag-memory/src /rag-memory/src
COPY rag-memory/package.json /rag-memory/package.json
COPY rag-memory/tsconfig.json /rag-memory/tsconfig.json

# Build TypeScript
RUN npm run build

# Production stage
FROM node:18-bullseye-slim

# 声明代理参数
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

# Set working directory
WORKDIR /app

# 设置代理环境变量
ENV http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    no_proxy=${NO_PROXY}

# Copy package files
COPY rag_memory_service/package.json ./
COPY rag-memory/package.json /rag-memory/package.json

# Copy rag-memory source and build it
COPY rag-memory /rag-memory
WORKDIR /rag-memory
RUN npm install && npm run build
WORKDIR /app

# Install all dependencies (rag-memory is now built)
RUN npm install

# Copy built files from builder
COPY --from=builder /app/dist ./dist

# Create directories
RUN mkdir -p /app/knowledge_docs /app/data

# Set environment variables
ENV NODE_ENV=production \
    PORT=3001 \
    DOCUMENTS_PATH=/app/knowledge_docs \
    INDEX_PATH=/app/data/memory.sqlite

# Expose port
EXPOSE 3001

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3001/', (r) => { process.exit(r.statusCode === 200 ? 0 : 1) })"

# Start service
CMD ["node", "dist/server.js"]
