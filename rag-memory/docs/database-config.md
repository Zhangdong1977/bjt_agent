# 数据库配置指南

## 支持的数据库

系统支持多种关系型数据库：
- PostgreSQL 12+
- MySQL 8.0+
- SQLite 3.35+
- Microsoft SQL Server 2019+

## PostgreSQL 配置

### 基础配置

```yaml
database:
  type: postgres
  host: localhost
  port: 5432
  database: myapp_db
  username: postgres
  password: secure_password
  ssl: true
  pool:
    min: 2
    max: 10
    acquireTimeoutMillis: 30000
  options:
    statement_timeout: 10000
    idle_in_transaction_session_timeout: 60000
```

### 连接字符串格式

```
postgresql://username:password@localhost:5432/myapp_db?sslmode=require
```

### 高级特性

**启用全文搜索**：
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_content_trgm ON documents USING gin(content gin_trgm_ops);
```

**分区表**：
```sql
CREATE TABLE logs (
    id SERIAL,
    created_at TIMESTAMP DEFAULT NOW(),
    data JSONB
) PARTITION BY RANGE (created_at);

CREATE TABLE logs_2024 PARTITION OF logs
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
```

## MySQL 配置

### 基础配置

```yaml
database:
  type: mysql
  host: localhost
  port: 3306
  database: myapp_db
  username: root
  password: secure_password
  charset: utf8mb4
  collation: utf8mb4_unicode_ci
  pool:
    min: 2
    max: 10
  timezone: '+08:00'
```

### 连接字符串格式

```
mysql://username:password@localhost:3306/myapp_db?charset=utf8mb4
```

### 性能优化

**InnoDB 配置**：
```ini
[mysqld]
innodb_buffer_pool_size = 2G
innodb_log_file_size = 256M
innodb_flush_log_at_trx_commit = 2
innodb_flush_method = O_DIRECT
```

**查询缓存**：
```sql
SET GLOBAL query_cache_size = 268435456;
SET GLOBAL query_cache_type = ON;
```

## SQLite 配置

### 文件数据库

```yaml
database:
  type: sqlite
  database: ./data/app.db
  mode: '0666'
  options:
    verbose: console
  pool:
    max: 1
```

### 内存数据库（测试用）

```yaml
database:
  type: sqlite
  database: ':memory:'
```

### 性能优化

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA cache_size = -64000;  -- 64MB cache
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 30000000000;
```

## 数据库迁移

### 创建迁移

```bash
npm run migration:create -- --name=add_users_table
```

生成的迁移文件：
```typescript
import { MigrationInterface, QueryRunner } from 'typeorm';

export class AddUsersTable1234567890 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE users (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
      )
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE users`);
  }
}
```

### 运行迁移

```bash
# 运行所有待处理的迁移
npm run migration:run

# 回滚最后一次迁移
npm run migration:revert

# 显示迁移状态
npm run migration:show
```

## 备份和恢复

### PostgreSQL 备份

```bash
# 全量备份
pg_dump -U postgres -d myapp_db > backup.sql

# 仅备份 schema
pg_dump -U postgres -d myapp_db --schema-only > schema.sql

# 仅备份数据
pg_dump -U postgres -d myapp_db --data-only > data.sql

# 恢复
psql -U postgres -d myapp_db < backup.sql
```

### MySQL 备份

```bash
# 全量备份
mysqldump -u root -p myapp_db > backup.sql

# 压缩备份
mysqldump -u root -p myapp_db | gzip > backup.sql.gz

# 恢复
mysql -u root -p myapp_db < backup.sql
```

### SQLite 备份

```bash
# 使用 .backup 命令
sqlite3 app.db ".backup 'backup.db'"

# 或直接复制文件
cp app.db app.db.backup
```

## 索引策略

### 选择合适的索引

```sql
-- B-tree 索引（默认，适合等值和范围查询）
CREATE INDEX idx_user_email ON users(email);

-- Hash 索引（仅适合等值查询）
CREATE INDEX idx_user_id_hash ON users USING HASH (id);

-- 复合索引
CREATE INDEX idx_user_status_date ON users(status, created_at);

-- 部分索引
CREATE INDEX idx_active_users ON users(email)
WHERE status = 'active';

-- 表达式索引
CREATE INDEX idx_user_lower_email ON users(LOWER(email));
```

### 索引维护

```sql
-- 检查索引使用情况
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
ORDER BY idx_scan ASC;

-- 重建索引
REINDEX TABLE users;

-- 删除未使用的索引
DROP INDEX IF EXISTS idx_unused;
```

## 查询优化

### EXPLAIN 分析

```sql
-- PostgreSQL
EXPLAIN ANALYZE SELECT * FROM users WHERE email = 'test@example.com';

-- MySQL
EXPLAIN SELECT * FROM users WHERE email = 'test@example.com';
```

### 慢查询日志

**PostgreSQL**：
```sql
ALTER SYSTEM SET log_min_duration_statement = 1000;
SELECT pg_reload_conf();
```

**MySQL**：
```ini
slow_query_log = 1
long_query_time = 1
slow_query_log_file = /var/log/mysql/slow.log
```

## 数据库监控

### 关键指标

1. **连接数**：当前活跃连接数
2. **查询性能**：平均查询响应时间
3. **锁等待**：表锁和行锁等待次数
4. **缓存命中率**：缓冲池命中率
5. **磁盘 I/O**：读写操作次数

### 推荐工具

- **pgAdmin**（PostgreSQL）
- **MySQL Workbench**（MySQL）
- **Datadog**（跨平台监控）
- **Prometheus + Grafana**（开源监控）
