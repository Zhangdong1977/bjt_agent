# Production Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Bid Review Agent System 以生产级别方式部署到服务器，对外服务端口 8000

**Architecture:** 使用 Docker 容器化 PostgreSQL + Redis，Nginx 反向代理（80: 前端静态文件, 8000: Backend API），bjt.sh 管理应用进程

**Tech Stack:** Docker, Docker Compose, Nginx, uvicorn, Celery, PostgreSQL, Redis

---

## File Structure

```
/home/bjt/bjt_agent/
├── docker-compose.yml              # 新建: PostgreSQL + Redis 服务
├── deploy/
│   └── nginx/
│       └── bjt                    # 新建: Nginx 配置文件
├── .env                           # 新建: 环境变量配置
├── scripts/
│   └── bjt.sh                     # 修改: 适配生产环境
└── frontend/
    └── dist/                      # 构建: 前端生产构建产物
```

---

## Task 1: 创建 Docker Compose 配置 (PostgreSQL + Redis)

**Files:**
- Create: `/home/bjt/bjt_agent/docker-compose.yml`

- [ ] **Step 1: 创建 docker-compose.yml**

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15
    container_name: bjt-postgres
    environment:
      POSTGRES_DB: bjt_db
      POSTGRES_USER: bjt_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped
    networks:
      - bjt-network

  redis:
    image: redis:7-alpine
    container_name: bjt-redis
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped
    networks:
      - bjt-network

volumes:
  postgres_data:
  redis_data:

networks:
  bjt-network:
    driver: bridge
```

- [ ] **Step 2: 提交**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-compose for PostgreSQL and Redis"
```

---

## Task 2: 创建 .env 环境变量配置

**Files:**
- Create: `/home/bjt/bjt_agent/.env`

- [ ] **Step 1: 创建 .env 文件**

```
POSTGRES_PASSWORD=your_secure_password_here
POSTGRES_HOST=localhost
POSTGRES_DB=bjt_db
POSTGRES_USER=bjt_user
POSTGRES_PORT=5432

REDIS_HOST=localhost
REDIS_PORT=6379
```

- [ ] **Step 2: 提交**

```bash
git add .env
git commit -m "feat: add environment variables for deployment"
```

---

## Task 3: 创建 Nginx 配置文件

**Files:**
- Create: `/home/bjt/bjt_agent/deploy/nginx/bjt`

- [ ] **Step 1: 创建 Nginx 配置目录和文件**

```nginx
# Frontend 静态文件服务 (端口 80)
server {
    listen 80;
    server_name _;

    root /home/bjt/bjt_agent/frontend/dist;
    index index.html;

    # 前端路由支持
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 静态资源缓存
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}

# Backend API 反向代理 (端口 8000)
server {
    listen 8000;
    server_name _;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 支持
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400;
    }
}
```

- [ ] **Step 2: 创建部署脚本**

```bash
mkdir -p /home/bjt/bjt_agent/deploy/nginx
# 将上面的配置写入 /home/bjt/bjt_agent/deploy/nginx/bjt
```

- [ ] **Step 3: 提交**

```bash
git add deploy/nginx/bjt
git commit -m "feat: add Nginx configuration for production"
```

---

## Task 4: 构建前端生产版本

**Files:**
- Modify: `/home/bjt/bjt_agent/frontend/vite.config.js` (构建输出配置)

- [ ] **Step 1: 执行前端构建**

```bash
cd /home/bjt/bjt_agent/frontend
npm install
npm run build
```

- [ ] **Step 2: 验证构建产物**

```bash
ls -la /home/bjt/bjt_agent/frontend/dist/
# 应该看到 index.html 和 assets/ 目录
```

---

## Task 5: 配置 Nginx 并启用

- [ ] **Step 1: 复制 Nginx 配置**

```bash
sudo cp /home/bjt/bjt_agent/deploy/nginx/bjt /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/bjt /etc/nginx/sites-enabled/
```

- [ ] **Step 2: 测试 Nginx 配置**

```bash
sudo nginx -t
```

- [ ] **Step 3: 重载 Nginx**

```bash
sudo systemctl reload nginx
```

---

## Task 6: 启动 Docker 服务 (PostgreSQL + Redis)

- [ ] **Step 1: 启动容器**

```bash
cd /home/bjt/bjt_agent
docker-compose up -d
```

- [ ] **Step 2: 验证容器运行**

```bash
docker ps
# 应该看到 bjt-postgres 和 bjt-redis 容器
```

- [ ] **Step 3: 验证 PostgreSQL 连接**

```bash
docker exec bjt-postgres pg_isready -U bjt_user
```

- [ ] **Step 4: 验证 Redis 连接**

```bash
docker exec bjt-redis redis-cli ping
# 应该返回 PONG
```

---

## Task 7: 修改 bjt.sh 适配生产环境

**Files:**
- Modify: `/home/bjt/bjt_agent/scripts/bjt.sh`

- [ ] **Step 1: 修改 Backend 启动命令为生产模式**

修改 `start_backend` 函数:

```bash
start_backend() {
    log "Starting Backend API Server..."
    cd "$BACKEND_DIR"
    # 生产模式: 使用 uvicorn --workers
    python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4 > "$SCRIPT_DIR/logs/backend.log" 2>&1 &
    save_pid "backend" "$!"
    log "Backend API started (PID: $(get_pid backend))"
}
```

- [ ] **Step 2: 提交**

```bash
git add scripts/bjt.sh
git commit -m "feat: update bjt.sh for production deployment"
```

---

## Task 8: 关闭防火墙

- [ ] **Step 1: 关闭 UFW**

```bash
sudo ufw disable
```

- [ ] **Step 2: 验证防火墙状态**

```bash
sudo ufw status
# 应该显示 Status: inactive
```

---

## Task 9: 启动应用服务

- [ ] **Step 1: 激活 conda 环境并启动**

```bash
cd /home/bjt/bjt_agent
conda activate ssirs
./scripts/bjt.sh start
```

- [ ] **Step 2: 检查服务状态**

```bash
./scripts/bjt.sh status
```

---

## Task 10: 验证部署

- [ ] **Step 1: 验证 Nginx 端口 80 (前端)**

```bash
curl -I http://localhost
# HTTP/1.1 200 OK
```

- [ ] **Step 2: 验证 Nginx 端口 8000 (API)**

```bash
curl -I http://localhost:8000/health
# HTTP/1.1 200 OK
```

- [ ] **Step 3: 验证 API 文档**

```bash
curl -I http://localhost:8000/docs
# HTTP/1.1 200 OK
```

- [ ] **Step 4: 验证 RAG Memory**

```bash
curl http://localhost:3001/api/status
```

---

## 验证清单

- [ ] Docker 容器运行中 (`docker ps`)
- [ ] PostgreSQL 连接正常
- [ ] Redis 连接正常
- [ ] Backend API 健康 (`curl http://localhost:8000/health`)
- [ ] RAG Memory 健康 (`curl http://localhost:3001/api/status`)
- [ ] Nginx 端口 80 可访问 (`curl http://localhost`)
- [ ] Nginx 端口 8000 API 代理正常 (`curl http://localhost:8000/docs`)
- [ ] 防火墙已关闭

---

## 回滚步骤

如果部署失败:

```bash
# 停止所有服务
./scripts/bjt.sh stop

# 停止 Docker
docker-compose down

# 移除 Nginx 配置
sudo rm /etc/nginx/sites-enabled/bjt
sudo systemctl reload nginx
```
