# Bid Review Agent System - 生产环境部署设计

## 概述

本设计描述如何将 Bid Review Agent System（Vue3/FastAPI 全栈应用）以生产级别方式部署到服务器，对外服务端口为 8000。

## 部署架构

```
                         ┌─────────────────────────────────────┐
                         │            服务器 (内网)               │
                         │                                     │
  用户 ───:8000──► Nginx ────► Backend API (uvicorn :8000)      │
                    │              │                            │
                    │              ├── Celery Review Worker     │
                    │              └── Celery Parser Worker     │
                    │                                          │
                    └──► Frontend Static (dist/ :80)           │
                         │                                     │
                         └─────────────────────────────────────┘
                                      │
                         ┌────────────┴───────────┐
                         │    Docker Network      │
                    PostgreSQL              Redis
                    (Docker)              (Docker)
                         │
  RAG Memory ────► (Node.js npm run dev, 由 bjt.sh 管理)
```

## 组件说明

| 组件 | 部署方式 | 端口 | 备注 |
|------|----------|------|------|
| Nginx | 系统包/apt | 80, 8000 | 反向代理 |
| Frontend | Nginx 静态文件 | 80 | `npm run build` 构建 |
| Backend API | bjt.sh (uvicorn) | 8000 | |
| Celery Workers | bjt.sh | - | 2个队列: review, parser |
| RAG Memory | bjt.sh (npm) | 3001 | Node.js 开发模式 |
| PostgreSQL | Docker 容器 | 5432 | 内部访问 |
| Redis | Docker 容器 | 6379 | 内部访问 |

## 部署步骤

### 1. 环境准备

#### 1.1 安装系统依赖

```bash
# Ubuntu/Debian
apt update
apt install -y nginx docker.io docker-compose

# 启动 Docker
systemctl enable docker
systemctl start docker
```

#### 1.2 安装 Node.js (前端构建)

```bash
curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
apt install -y nodejs
```

#### 1.3 安装 Python 与 Conda

```bash
# 安装 Miniconda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
source ~/.bashrc

# 创建并激活环境
conda create -n ssirs python=3.10
conda activate ssirs
```

#### 1.4 安装后端依赖

```bash
cd /home/bjt/bjt_agent/backend
pip install -r requirements.txt
```

### 2. Docker 服务 (PostgreSQL + Redis)

#### 2.1 创建 docker-compose.yml

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

#### 2.2 配置环境变量

创建 `.env` 文件：

```bash
POSTGRES_PASSWORD=your_secure_password
POSTGRES_HOST=localhost
POSTGRES_DB=bjt_db
POSTGRES_USER=bjt_user
POSTGRES_PORT=5432

REDIS_HOST=localhost
REDIS_PORT=6379
```

#### 2.3 启动服务

```bash
docker-compose up -d
```

### 3. 前端构建

```bash
cd /home/bjt/bjt_agent/frontend
npm install
npm run build
```

构建产物在 `dist/` 目录。

### 4. Nginx 配置

#### 4.1 创建 Nginx 配置文件

```nginx
# /etc/nginx/sites-available/bjt

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

#### 4.2 启用配置

```bash
ln -s /etc/nginx/sites-available/bjt /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### 5. 启动应用

```bash
cd /home/bjt/bjt_agent
./scripts/bjt.sh start
```

### 6. 服务健康检查

```bash
./scripts/bjt.sh status
```

## 启动顺序

1. **Docker** (PostgreSQL + Redis) - `docker-compose up -d`
2. **应用服务** - `./scripts/bjt.sh start`
   - Celery Review Worker
   - Celery Parser Worker
   - RAG Memory Service
   - Backend API
   - Frontend (dev 模式)

## 验证清单

- [ ] Docker 容器运行中 (`docker ps`)
- [ ] PostgreSQL 连接正常 (`psql -h localhost -U bjt_user`)
- [ ] Redis 连接正常 (`redis-cli ping`)
- [ ] Backend API 健康 (`curl http://localhost:8000/health`)
- [ ] RAG Memory 健康 (`curl http://localhost:3001/api/status`)
- [ ] Nginx 端口 80 可访问 (`curl http://localhost`)
- [ ] Nginx 端口 8000 API 代理正常 (`curl http://localhost:8000/docs`)

## 故障排查

### Backend 无法启动
```bash
# 检查 conda 环境
conda activate ssirs

# 查看日志
tail -f /home/bjt/bjt_agent/scripts/logs/backend.log
```

### Celery Worker 不工作
```bash
# 检查 Redis 连接
redis-cli -h 183.66.37.186 -p 7005 ping

# 查看 Celery 日志
tail -f /home/bjt/bjt_agent/scripts/logs/celery_review.log
```

### Nginx 502 错误
```bash
# 检查后端是否运行
curl http://127.0.0.1:8000/health

# 检查 Nginx 错误日志
tail -f /var/log/nginx/error.log
```

## 备份策略

### 数据库备份
```bash
# PostgreSQL 备份
docker exec bjt-postgres pg_dump -U bjt_user bjt_db > backup_$(date +%Y%m%d).sql
```

### 定时备份 (crontab)
```bash
# 每天凌晨 3 点备份
0 3 * * * docker exec bjt-postgres pg_dump -U bjt_user bjt_db > /home/bjt/bjt_agent/backups/db_$(date +\%Y\%m\%d).sql
```

## 安全建议

1. **防火墙**: 仅开放 80 和 8000 端口
   ```bash
   ufw allow 80/tcp
   ufw allow 8000/tcp
   ufw enable
   ```

2. **环境变量**: `.env` 文件不要提交到 git

3. **定期更新**: 关注安全更新
   ```bash
   apt update && apt upgrade
   docker-compose pull
   ```
