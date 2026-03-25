# 安全性指南

## 认证与授权

### JWT Token 配置

```typescript
// utils/jwt.ts
import jwt from 'jsonwebtoken';

const JWT_SECRET = process.env.JWT_SECRET || 'your-secret-key';
const JWT_EXPIRES_IN = '1h';
const JWT_REFRESH_EXPIRES_IN = '7d';

export function generateToken(payload: object): string {
  return jwt.sign(payload, JWT_SECRET, {
    expiresIn: JWT_EXPIRES_IN,
  });
}

export function generateRefreshToken(payload: object): string {
  return jwt.sign(payload, JWT_SECRET, {
    expiresIn: JWT_REFRESH_EXPIRES_IN,
  });
}

export function verifyToken(token: string): any {
  try {
    return jwt.verify(token, JWT_SECRET);
  } catch (error) {
    throw new Error('Invalid token');
  }
}
```

### 密码策略

**密码强度要求**：
- 最少 8 个字符
- 必须包含大小写字母
- 必须包含数字
- 必须包含特殊字符

```typescript
// utils/passwordValidator.ts
import z from 'zod';

export const passwordSchema = z
  .string()
  .min(8, 'Password must be at least 8 characters')
  .regex(/[A-Z]/, 'Password must contain at least one uppercase letter')
  .regex(/[a-z]/, 'Password must contain at least one lowercase letter')
  .regex(/[0-9]/, 'Password must contain at least one number')
  .regex(/[^A-Za-z0-9]/, 'Password must contain at least one special character');

// 密码哈希
import bcrypt from 'bcrypt';

const SALT_ROUNDS = 12;

export async function hashPassword(password: string): Promise<string> {
  return bcrypt.hash(password, SALT_ROUNDS);
}

export async function comparePassword(
  password: string,
  hash: string
): Promise<boolean> {
  return bcrypt.compare(password, hash);
}
```

### 多因素认证 (MFA)

```typescript
// services/mfaService.ts
import speakeasy from 'speakeasy';
import QRCode from 'qrcode';

export class MFAService {
  // 生成 TOTP 密钥
  generateSecret(userEmail: string) {
    const secret = speakeasy.generateSecret({
      name: `MyApp (${userEmail})`,
      issuer: 'MyApp',
    });

    return {
      secret: secret.base32,
      qrCode: secret.otpauth_url,
    };
  }

  // 生成二维码
  async generateQRCode(otpauthUrl: string): Promise<string> {
    return QRCode.toDataURL(otpauthUrl);
  }

  // 验证 TOTP 代码
  verifyToken(secret: string, token: string): boolean {
    return speakeasy.totp.verify({
      secret,
      encoding: 'base32',
      token,
      window: 2, // 允许时间偏差
    });
  }
}
```

## 输入验证

### SQL 注入防护

使用 TypeORM 的参数化查询：

```typescript
// ❌ 不安全 - SQL 注入风险
const user = await queryRunner.query(
  `SELECT * FROM users WHERE email = '${email}'`
);

// ✅ 安全 - 参数化查询
const user = await userRepository.findOne({
  where: { email },
});
```

### XSS 防护

```typescript
// utils/sanitize.ts
import DOMPurify from 'dompurify';

export function sanitizeHTML(html: string): string {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'u', 'a'],
    ALLOWED_ATTR: ['href'],
  });
}

// 使用示例
app.post('/posts', async (req, res) => {
  const { content } = req.body;
  const sanitized = sanitizeHTML(content);
  // 保存到数据库
});
```

### CSRF 保护

```typescript
// middlewares/csrfMiddleware.ts
import csrf from 'csurf';

const csrfProtection = csrf({ cookie: true });

export default csrfProtection;

// 使用
app.post('/api/posts', csrfProtection, postController.create);

// 前端需要获取 CSRF token
app.get('/api/csrf-token', csrfProtection, (req, res) => {
  res.json({ csrfToken: req.csrfToken() });
});
```

## 数据保护

### 敏感数据加密

```typescript
// utils/encryption.ts
import crypto from 'crypto';

const ALGORITHM = 'aes-256-gcm';
const SECRET_KEY = crypto.scryptSync(process.env.ENCRYPTION_KEY!, 'salt', 32);
const IV_LENGTH = 16;

export function encrypt(text: string): string {
  const iv = crypto.randomBytes(IV_LENGTH);
  const cipher = crypto.createCipheriv(ALGORITHM, SECRET_KEY, iv);

  let encrypted = cipher.update(text, 'utf8', 'hex');
  encrypted += cipher.final('hex');

  const authTag = cipher.getAuthTag();

  return `${iv.toString('hex')}:${authTag.toString('hex')}:${encrypted}`;
}

export function decrypt(encrypted: string): string {
  const [ivHex, authTagHex, encryptedText] = encrypted.split(':');

  const iv = Buffer.from(ivHex, 'hex');
  const authTag = Buffer.from(authTagHex, 'hex');

  const decipher = crypto.createDecipheriv(ALGORITHM, SECRET_KEY, iv);
  decipher.setAuthTag(authTag);

  let decrypted = decipher.update(encryptedText, 'hex', 'utf8');
  decrypted += decipher.final('utf8');

  return decrypted;
}
```

### 数据脱敏

```typescript
// utils/masking.ts
export function maskEmail(email: string): string {
  const [username, domain] = email.split('@');
  const maskedUsername =
    username.slice(0, 2) + '*'.repeat(username.length - 2);
  return `${maskedUsername}@${domain}`;
}

export function maskPhoneNumber(phone: string): string {
  return phone.replace(/(\d{3})\d{4}(\d{4})/, '$1****$2');
}

export function maskCreditCard(card: string): string {
  return card.replace(/\d(?=\d{4})/g, '*');
}
```

## API 安全

### 速率限制

```typescript
// middlewares/rateLimitMiddleware.ts
import rateLimit from 'express-rate-limit';

export const apiLimiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 分钟
  max: 100, // 限制 100 次请求
  message: 'Too many requests from this IP',
  standardHeaders: true,
  legacyHeaders: false,
});

export const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 5, // 登录接口更严格
  skipSuccessfulRequests: true,
});

// 使用
app.use('/api/', apiLimiter);
app.use('/api/auth/login', authLimiter);
```

### CORS 配置

```typescript
// middlewares/corsMiddleware.ts
import cors from 'cors';

const corsOptions = {
  origin: function (origin: string | undefined, callback: any) {
    const allowedOrigins = [
      'https://example.com',
      'https://www.example.com',
    ];

    if (!origin || allowedOrigins.includes(origin)) {
      callback(null, true);
    } else {
      callback(new Error('Not allowed by CORS'));
    }
  },
  credentials: true,
  optionsSuccessStatus: 200,
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'],
  allowedHeaders: ['Content-Type', 'Authorization'],
};

export default cors(corsOptions);
```

### Helmet 安全头

```typescript
import helmet from 'helmet';

app.use(helmet());

// 自定义 CSP
app.use(
  helmet.contentSecurityPolicy({
    directives: {
      defaultSrc: ["'self'"],
      styleSrc: ["'self'", "'unsafe-inline'"],
      scriptSrc: ["'self'"],
      imgSrc: ["'self'", 'data:', 'https:'],
      connectSrc: ["'self'"],
    },
  })
);
```

## 权限控制

### RBAC (基于角色的访问控制)

```typescript
// middlewares/rbacMiddleware.ts
import { Request, Response, NextFunction } from 'express';

interface Permission {
  resource: string;
  action: string;
}

// 角色权限映射
const ROLE_PERMISSIONS: Record<string, Permission[]> = {
  admin: [
    { resource: '*', action: '*' }, // 所有权限
  ],
  editor: [
    { resource: 'posts', action: 'create' },
    { resource: 'posts', action: 'update' },
    { resource: 'posts', action: 'delete' },
    { resource: 'comments', action: 'moderate' },
  ],
  user: [
    { resource: 'posts', action: 'read' },
    { resource: 'comments', action: 'create' },
  ],
};

export function requirePermission(resource: string, action: string) {
  return (req: Request, res: Response, next: NextFunction) => {
    const userRole = req.userRole;

    if (!userRole) {
      return res.status(401).json({ error: 'Unauthorized' });
    }

    const permissions = ROLE_PERMISSIONS[userRole] || [];

    const hasPermission = permissions.some((p) =>
      p.resource === '*' || (p.resource === resource && (p.action === '*' || p.action === action))
    );

    if (!hasPermission) {
      return res.status(403).json({ error: 'Forbidden' });
    }

    next();
  };
}

// 使用示例
app.post(
  '/api/posts',
  authMiddleware,
  requirePermission('posts', 'create'),
  postController.create
);
```

## 日志和监控

### 安全事件日志

```typescript
// utils/securityLogger.ts
import { logger } from './logger';

export function logSecurityEvent(event: {
  type: 'auth_failure' | 'auth_success' | 'permission_denied' | 'suspicious_activity';
  userId?: string;
  ip: string;
  userAgent: string;
  details?: any;
}) {
  logger.warn('Security Event', {
    event_type: event.type,
    user_id: event.userId,
    ip: event.ip,
    user_agent: event.userAgent,
    details: event.details,
    timestamp: new Date().toISOString(),
  });
}

// 使用示例
logSecurityEvent({
  type: 'auth_failure',
  ip: req.ip,
  userAgent: req.get('User-Agent'),
  details: { reason: 'Invalid password' },
});
```

### 异常检测

```typescript
// middlewares/anomalyDetection.ts
import { Request, Response, NextFunction } from 'express';
import { logSecurityEvent } from '../utils/securityLogger';

const requestCounts = new Map<string, number[]>();

export function detectAnomalies(req: Request, res: Response, next: NextFunction) {
  const ip = req.ip;
  const now = Date.now();

  // 获取该 IP 的请求记录
  let timestamps = requestCounts.get(ip) || [];

  // 清理 1 分钟前的记录
  timestamps = timestamps.filter(t => now - t < 60000);

  // 添加当前请求
  timestamps.push(now);

  // 检查是否超过阈值（1分钟内超过 60 次）
  if (timestamps.length > 60) {
    logSecurityEvent({
      type: 'suspicious_activity',
      ip,
      userAgent: req.get('User-Agent') || '',
      details: { reason: 'Rate limit exceeded suspiciously' },
    });

    return res.status(429).json({ error: 'Too many requests' });
  }

  requestCounts.set(ip, timestamps);
  next();
}
```

## 环境变量管理

```bash
# .env.example
NODE_ENV=production
JWT_SECRET=your-super-secret-jwt-key-change-this
ENCRYPTION_KEY=your-32-character-encryption-key

DATABASE_URL=postgresql://user:password@localhost:5432/db
REDIS_HOST=localhost
REDIS_PORT=6379

ALLOWED_ORIGINS=https://example.com,https://www.example.com

# 第三方服务
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-email@example.com
SMTP_PASS=your-password

# 监控和日志
SENTRY_DSN=https://xxx@sentry.io/xxx
LOG_LEVEL=info
```

```typescript
// config/env.ts
import dotenv from 'dotenv';
import { z } from 'zod';

dotenv.config();

const envSchema = z.object({
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  PORT: z.string().transform(Number).default('3000'),
  JWT_SECRET: z.string().min(32),
  ENCRYPTION_KEY: z.string().length(32),
  DATABASE_URL: z.string().url(),
  REDIS_HOST: z.string(),
  REDIS_PORT: z.string().transform(Number),
});

export const env = envSchema.parse(process.env);
```

## 安全最佳实践清单

### 开发阶段
- ✅ 所有输入都经过验证和清理
- ✅ 使用参数化查询防止 SQL 注入
- ✅ 敏感数据加密存储
- ✅ 密码使用 bcrypt 哈希
- ✅ 实施 CSRF 保护
- ✅ 配置正确的 CORS 策略

### 生产部署
- ✅ 使用 HTTPS（TLS 1.3）
- ✅ 启用安全 HTTP 头（Helmet）
- ✅ 实施速率限制
- ✅ 配置 WAF（Web Application Firewall）
- ✅ 定期更新依赖包
- ✅ 启用日志监控和告警
- ✅ 配置数据库备份策略
- ✅ 使用环境变量管理密钥

### 日常维护
- ✅ 定期安全审计
- ✅ 监控异常登录和访问模式
- ✅ 实施密码过期策略
- ✅ 定期轮换 API 密钥
- ✅ 保留安全事件日志
- ✅ 制定应急响应计划
