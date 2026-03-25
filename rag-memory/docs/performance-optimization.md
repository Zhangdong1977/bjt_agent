# 性能优化指南

## 数据库优化

### 查询优化

```sql
-- 使用索引覆盖
CREATE INDEX idx_user_email_verified ON users(email, is_verified);

-- 优化 JOIN 查询
SELECT u.id, u.name, p.title
FROM users u
INNER JOIN posts p ON u.id = p.author_id
WHERE u.is_active = true
  AND p.published = true;

-- 使用 EXPLAIN 分析
EXPLAIN ANALYZE SELECT * FROM posts WHERE author_id = 123;
```

### 批量操作

```typescript
// ❌ 不推荐 - 循环插入
for (const user of users) {
  await userRepository.save(user);
}

// ✅ 推荐 - 批量插入
await userRepository.save(users, { chunk: 100 });

// ✅ 或者使用批量插入
await queryRunner.manager
  .createQueryBuilder()
  .insert()
  .into(User)
  .values(users)
  .execute();
```

### 连接池配置

```typescript
// 数据库连接池配置
{
  type: 'postgres',
  host: 'localhost',
  port: 5432,
  username: 'user',
  password: 'pass',
  database: 'db',
  entities: ['dist/entities/**/*.js'],
  synchronize: false,
  logging: false,

  // 连接池配置
  poolSize: 20,           // 最大连接数
  extra: {
    max: 20,              // 池中最大连接数
    min: 5,               // 池中最小连接数
    idleTimeoutMillis: 30000,  // 空闲连接超时
    connectionTimeoutMillis: 2000,  // 连接超时
  },
}
```

## 缓存策略

### Redis 多级缓存

```typescript
// services/cacheService.ts
import Redis from 'ioredis';

const redis = new Redis({
  host: 'localhost',
  port: 6379,
  db: 0,
});

export class CacheService {
  // L1 缓存：内存缓存（最快，容量小）
  private memoryCache = new Map<string, { value: any; expiry: number }>();

  // L2 缓存：Redis（快，容量中）
  async get(key: string): Promise<any> {
    // 先查内存缓存
    const memValue = this.memoryCache.get(key);
    if (memValue && memValue.expiry > Date.now()) {
      return memValue.value;
    }

    // 再查 Redis
    const redisValue = await redis.get(key);
    if (redisValue) {
      const parsed = JSON.parse(redisValue);
      // 回填内存缓存
      this.memoryCache.set(key, {
        value: parsed,
        expiry: Date.now() + 60000, // 1分钟
      });
      return parsed;
    }

    return null;
  }

  async set(key: string, value: any, ttl: number = 3600): Promise<void> {
    // 同时写入内存和 Redis
    this.memoryCache.set(key, {
      value,
      expiry: Date.now() + 60000,
    });

    await redis.setex(key, ttl, JSON.stringify(value));
  }

  async invalidate(pattern: string): Promise<void> {
    // 清除内存缓存
    for (const key of this.memoryCache.keys()) {
      if (key.match(pattern)) {
        this.memoryCache.delete(key);
      }
    }

    // 清除 Redis 缓存
    const keys = await redis.keys(pattern);
    if (keys.length > 0) {
      await redis.del(...keys);
    }
  }
}
```

### 缓存预热

```typescript
// scripts/cacheWarmup.ts
import { PostRepository } from '../repositories/PostRepository';

export async function warmupCache() {
  const postRepo = new PostRepository();
  const cacheService = new CacheService();

  // 预加载热门文章
  const popularPosts = await postRepo.findPopular(100);

  for (const post of popularPosts) {
    await cacheService.set(`post:${post.id}`, post, 3600);
  }

  // 预加载首页数据
  const featuredPosts = await postRepo.findFeatured(10);
  await cacheService.set('home:featured', featuredPosts, 1800);
}
```

## API 性能

### 响应压缩

```typescript
import compression from 'compression';

app.use(compression({
  filter: (req, res) => {
    if (req.headers['x-no-compression']) {
      return false;
    }
    return compression.filter(req, res);
  },
  threshold: 1024, // 只压缩大于 1KB 的响应
}));
```

### 分页优化

```typescript
// 使用游标分页代替偏移量分页
async function getPaginatedPosts(cursor: string | null, limit: number = 10) {
  let query = this.postRepository
    .createQueryBuilder('post')
    .orderBy('post.id', 'ASC')
    .limit(limit);

  if (cursor) {
    query = query.where('post.id > :cursor', { cursor });
  }

  const posts = await query.getMany();
  const nextCursor = posts.length > 0 ? posts[posts.length - 1].id : null;

  return { posts, nextCursor };
}
```

### 字段选择

```typescript
// 只查询需要的字段
async function getUserList() {
  return this.userRepository.find({
    select: ['id', 'name', 'email'], // 只选择这些字段
    where: { isActive: true },
  });
}
```

## 前端优化

### 代码分割

```typescript
// 路由级别代码分割
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Settings = lazy(() => import('./pages/Settings'));

// 组件级别代码分割
const HeavyChart = lazy(() => import('./components/HeavyChart'));

function App() {
  return (
    <Suspense fallback={<Skeleton />}>
      <Routes>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </Suspense>
  );
}
```

### 图片优化

```tsx
import Image from 'next/image';

// 使用 Next.js Image 组件
<Image
  src="/profile.jpg"
  alt="Profile"
  width={400}
  height={300}
  priority={false} // 懒加载
  placeholder="blur" // 模糊占位符
/>

// 响应式图片
<picture>
  <source srcSet="image.avif" type="image/avif" />
  <source srcSet="image.webp" type="image/webp" />
  <img src="image.jpg" alt="Description" loading="lazy" />
</picture>
```

### 虚拟滚动

```tsx
import { useVirtualizer } from '@tanstack/react-virtual';

function VirtualList({ items }: { items: Item[] }) {
  const parentRef = useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 100, // 每行高度
    overscan: 5, // 额外渲染的行数
  });

  return (
    <div ref={parentRef} style={{ height: '600px', overflow: 'auto' }}>
      <div style={{ height: `${rowVirtualizer.getTotalSize()}px` }}>
        {rowVirtualizer.getVirtualItems().map((virtualItem) => (
          <div
            key={virtualItem.key}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: `${virtualItem.size}px`,
              transform: `translateY(${virtualItem.start}px)`,
            }}
          >
            {items[virtualItem.index].content}
          </div>
        ))}
      </div>
    </div>
  );
}
```

## 内存优化

### 流式处理

```typescript
// ❌ 不推荐 - 一次性加载所有数据
const allUsers = await userRepository.find();
for (const user of allUsers) {
  processUser(user);
}

// ✅ 推荐 - 流式处理
const stream = await userRepository
  .createQueryBuilder()
  .stream();

for await (const user of stream) {
  processUser(user);
}
```

### 对象池

```typescript
// utils/objectPool.ts
export class ObjectPool<T> {
  private pool: T[] = [];

  constructor(
    private factory: () => T,
    private reset: (obj: T) => void,
    initialSize: number = 10
  ) {
    for (let i = 0; i < initialSize; i++) {
      this.pool.push(factory());
    }
  }

  acquire(): T {
    return this.pool.pop() || this.factory();
  }

  release(obj: T): void {
    this.reset(obj);
    this.pool.push(obj);
  }
}

// 使用示例
const bufferPool = new ObjectPool(
  () => new ArrayBuffer(1024),
  () => {}, // 清理函数
  100
);

const buffer = bufferPool.acquire();
// 使用 buffer
bufferPool.release(buffer);
```

## 并发控制

### 请求并发限制

```typescript
// utils/concurrency.ts
import pLimit from 'p-limit';

export async function processConcurrently<T, R>(
  items: T[],
  processor: (item: T) => Promise<R>,
  concurrency: number = 5
): Promise<R[]> {
  const limit = pLimit(concurrency);

  const tasks = items.map((item) =>
    limit(() => processor(item))
  );

  return Promise.all(tasks);
}

// 使用示例
const results = await processConcurrently(
  userIds,
  (id) => fetchUserData(id),
  10 // 最多 10 个并发请求
);
```

### 防抖和节流

```typescript
// hooks/useDebounce.ts
export function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => clearTimeout(handler);
  }, [value, delay]);

  return debouncedValue;
}

// hooks/useThrottle.ts
export function useThrottle<T>(value: T, limit: number): T {
  const [throttledValue, setThrottledValue] = useState(value);
  const lastRan = useRef(Date.now());

  useEffect(() => {
    const handler = setTimeout(() => {
      if (Date.now() - lastRan.current >= limit) {
        setThrottledValue(value);
        lastRan.current = Date.now();
      }
    }, limit - (Date.now() - lastRan.current));

    return () => clearTimeout(handler);
  }, [value, limit]);

  return throttledValue;
}
```

## 监控和分析

### 性能监控

```typescript
// middlewares/performanceMiddleware.ts
import { Request, Response, NextFunction } from 'express';

export function performanceMiddleware(req: Request, res: Response, next: NextFunction) {
  const startTime = Date.now();

  res.on('finish', () => {
    const duration = Date.now() - startTime;
    const memory = process.memoryUsage();

    console.log({
      method: req.method,
      url: req.url,
      status: res.statusCode,
      duration: `${duration}ms`,
      memory: {
        heapUsed: `${Math.round(memory.heapUsed / 1024 / 1024)}MB`,
        heapTotal: `${Math.round(memory.heapTotal / 1024 / 1024)}MB`,
      },
    });

    // 记录慢查询
    if (duration > 1000) {
      console.warn(`Slow request: ${req.method} ${req.url} took ${duration}ms`);
    }
  });

  next();
}
```

### APM 集成

```typescript
// 监控 Node.js 性能
import apm from 'elastic-apm-node';

apm.start({
  serviceName: 'my-app',
  serverUrl: process.env.APM_SERVER_URL,
  secretToken: process.env.APM_SECRET_TOKEN,
  environment: process.env.NODE_ENV,
  logLevel: 'info',
});

// 使用 APM 事务
app.use((req, res, next) => {
  const transaction = apm.startTransaction(`${req.method} ${req.path}`, 'request');
  res.on('finish', () => {
    transaction?.end();
  });
  next();
});
```

## 性能测试

### 负载测试

```javascript
// loadtest.js
import autocannon from 'autocannon';

const result = await autocannon({
  url: 'http://localhost:3000/api/posts',
  connections: 100,        // 并发连接数
  duration: 30,            // 测试持续时间（秒）
  pipelining: 1,           // 每个连接的流水线请求数
  amount: 10000,           // 总请求数（可选）
});

console.log('Latency avg:', result.latency.mean);
console.log('Requests/sec:', result.requests.mean);
console.log('Throughput:', result.throughput.mean);
```

### 基准测试

```typescript
import Benchmark from 'benchmark';

const suite = new Benchmark.Suite();

suite
  .add('RegExp#test', () => {
    /o/.test('Hello World!');
  })
  .add('String#indexOf', () => {
    'Hello World!'.indexOf('o') > -1;
  })
  .on('cycle', (event) => {
    console.log(String(event.target));
  })
  .on('complete', function() {
    console.log('Fastest is ' + this.filter('fastest').map('name'));
  })
  .run({ async: true });
```

## 性能优化清单

### 数据库
- ✅ 为常用查询字段创建索引
- ✅ 使用批量操作代替循环单条操作
- ✅ 优化连接池大小
- ✅ 使用 EXPLAIN 分析慢查询
- ✅ 考虑数据分区和分表

### 缓存
- ✅ 实施 Redis 多级缓存
- ✅ 使用缓存预热
- ✅ 设置合理的 TTL
- ✅ 监控缓存命中率

### API
- ✅ 启用响应压缩
- ✅ 使用游标分页
- ✅ 只查询需要的字段
- ✅ 实施 CDN 缓存静态资源

### 前端
- ✅ 代码分割和懒加载
- ✅ 图片优化和懒加载
- ✅ 虚拟滚动长列表
- ✅ 使用 memo 优化重渲染

### 监控
- ✅ 设置性能监控
- ✅ 定期进行负载测试
- ✅ 建立性能基线
- ✅ 持续优化瓶颈点
