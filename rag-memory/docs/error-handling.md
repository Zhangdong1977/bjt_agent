# 错误处理指南

## 错误类型定义

### 自定义错误类

```typescript
// utils/errors.ts
export class AppError extends Error {
  constructor(
    public statusCode: number,
    public message: string,
    public isOperational = true,
    public code?: string
  ) {
    super(message);
    Object.setPrototypeOf(this, AppError.prototype);
    Error.captureStackTrace(this, this.constructor);
  }
}

export class BadRequestError extends AppError {
  constructor(message: string = 'Bad Request', code?: string) {
    super(400, message, true, code || 'BAD_REQUEST');
  }
}

export class UnauthorizedError extends AppError {
  constructor(message: string = 'Unauthorized', code?: string) {
    super(401, message, true, code || 'UNAUTHORIZED');
  }
}

export class ForbiddenError extends AppError {
  constructor(message: string = 'Forbidden', code?: string) {
    super(403, message, true, code || 'FORBIDDEN');
  }
}

export class NotFoundError extends AppError {
  constructor(message: string = 'Resource not found', code?: string) {
    super(404, message, true, code || 'NOT_FOUND');
  }
}

export class ConflictError extends AppError {
  constructor(message: string = 'Conflict', code?: string) {
    super(409, message, true, code || 'CONFLICT');
  }
}

export class ValidationError extends AppError {
  constructor(public details: any[], message: string = 'Validation failed') {
    super(422, message, true, 'VALIDATION_ERROR');
    this.details = details;
  }
}

export class RateLimitError extends AppError {
  constructor(retryAfter?: number) {
    super(
      429,
      'Too many requests',
      true,
      'RATE_LIMIT_EXCEEDED'
    );
    this.retryAfter = retryAfter;
  }
  retryAfter?: number;
}
```

## 全局错误处理

### Express 错误中间件

```typescript
// middlewares/errorHandler.ts
import { Request, Response, NextFunction } from 'express';
import { ZodError } from 'zod';
import { AppError } from '../utils/errors';
import { logger } from '../utils/logger';

export function errorHandler(
  error: Error,
  req: Request,
  res: Response,
  next: NextFunction
) {
  // 记录错误
  logger.error('Error occurred', {
    error: error.message,
    stack: error.stack,
    url: req.url,
    method: req.method,
    ip: req.ip,
    userAgent: req.get('User-Agent'),
  });

  // 处理已知错误
  if (error instanceof AppError) {
    return res.status(error.statusCode).json({
      success: false,
      error: {
        code: error.code,
        message: error.message,
        ...(error instanceof ValidationError && { details: error.details }),
        ...(error instanceof RateLimitError && {
          retryAfter: error.retryAfter,
        }),
      },
    });
  }

  // 处理 Zod 验证错误
  if (error instanceof ZodError) {
    return res.status(400).json({
      success: false,
      error: {
        code: 'VALIDATION_ERROR',
        message: 'Input validation failed',
        details: error.errors.map((e) => ({
          field: e.path.join('.'),
          message: e.message,
        })),
      },
    });
  }

  // 处理 JSON 解析错误
  if (error instanceof SyntaxError && 'body' in error) {
    return res.status(400).json({
      success: false,
      error: {
        code: 'INVALID_JSON',
        message: 'Invalid JSON in request body',
      },
    });
  }

  // 处理未知错误
  res.status(500).json({
    success: false,
    error: {
      code: 'INTERNAL_SERVER_ERROR',
      message: process.env.NODE_ENV === 'production'
        ? 'An unexpected error occurred'
        : error.message,
      ...(process.env.NODE_ENV !== 'production' && { stack: error.stack }),
    },
  });
}

// 404 处理
export function notFoundHandler(req: Request, res: Response) {
  res.status(404).json({
    success: false,
    error: {
      code: 'NOT_FOUND',
      message: `Route ${req.method} ${req.url} not found`,
    },
  });
}
```

### Async 包装器

```typescript
// middlewares/asyncHandler.ts
import { Request, Response, NextFunction } from 'express';

export function asyncHandler(
  fn: (req: Request, res: Response, next: NextFunction) => Promise<any>
) {
  return (req: Request, res: Response, next: NextFunction) => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
}

// 使用示例
router.get(
  '/users/:id',
  asyncHandler(async (req, res) => {
    const user = await userService.findById(req.params.id);
    res.json({ success: true, data: user });
  })
);
```

## 业务逻辑错误处理

### Service 层错误

```typescript
// services/UserService.ts
import { NotFoundError, ConflictError, BadRequestError } from '../utils/errors';

export class UserService {
  async getUserById(id: string) {
    const user = await this.userRepository.findOne({ where: { id } });

    if (!user) {
      throw new NotFoundError('User not found', 'USER_NOT_FOUND');
    }

    return user;
  }

  async createUser(data: CreateUserDto) {
    // 检查邮箱是否已存在
    const existing = await this.userRepository.findOne({
      where: { email: data.email },
    });

    if (existing) {
      throw new ConflictError('Email already registered', 'EMAIL_EXISTS');
    }

    // 验证密码强度
    if (!this.isPasswordStrong(data.password)) {
      throw new BadRequestError(
        'Password does not meet security requirements',
        'WEAK_PASSWORD'
      );
    }

    // 创建用户
    const user = await this.userRepository.create(data);
    return this.userRepository.save(user);
  }

  async transferMoney(fromId: string, toId: string, amount: number) {
    const fromUser = await this.getUserById(fromId);
    const toUser = await this.getUserById(toId);

    if (fromUser.balance < amount) {
      throw new BadRequestError('Insufficient balance', 'INSUFFICIENT_FUNDS');
    }

    // 使用事务确保原子性
    await this.userRepository.transaction(async (transactionalEntityManager) => {
      await transactionalEntityManager decrement(User, fromUser.id, 'balance', amount);
      await transactionalEntityManager increment(User, toUser.id, 'balance', amount);
    });
  }

  private isPasswordStrong(password: string): boolean {
    // 密码强度验证逻辑
    return password.length >= 8 &&
           /[A-Z]/.test(password) &&
           /[a-z]/.test(password) &&
           /[0-9]/.test(password);
  }
}
```

## 数据库错误处理

### TypeORM 错误处理

```typescript
// middlewares/databaseErrorHandler.ts
import { QueryFailedError } from 'typeorm';
import { AppError } from '../utils/errors';

export function handleDatabaseError(error: Error): AppError {
  if (error instanceof QueryFailedError) {
    // PostgreSQL 唯一约束违反
    if (error.driverError.code === '23505') {
      const detail = error.driverError.detail;
      const field = detail.match(/Key \(([^)]+)\)/)?.[1];
      return new ConflictError(
        `${field} already exists`,
        'DUPLICATE_ENTRY'
      );
    }

    // PostgreSQL 外键约束违反
    if (error.driverError.code === '23503') {
      return new BadRequestError(
        'Referenced resource does not exist',
        'FOREIGN_KEY_VIOLATION'
      );
    }

    // PostgreSQL 检查约束违反
    if (error.driverError.code === '23514') {
      return new BadRequestError(
        'Data constraint violation',
        'CONSTRAINT_VIOLATION'
      );
    }
  }

  // 连接错误
  if (error.message.includes('ECONNREFUSED')) {
    return new AppError(
      503,
      'Database connection failed',
      true,
      'DATABASE_UNAVAILABLE'
    );
  }

  // 其他数据库错误
  return new AppError(
    500,
    'Database operation failed',
    false,
    'DATABASE_ERROR'
  );
}
```

## 外部 API 错误处理

### HTTP 客户端错误处理

```typescript
// utils/httpClient.ts
import axios, { AxiosError } from 'axios';
import { AppError } from './errors';

export class HttpClient {
  async get(url: string, config?: any) {
    try {
      const response = await axios.get(url, config);
      return response.data;
    } catch (error) {
      throw this.handleAxiosError(error);
    }
  }

  async post(url: string, data?: any, config?: any) {
    try {
      const response = await axios.post(url, data, config);
      return response.data;
    } catch (error) {
      throw this.handleAxiosError(error);
    }
  }

  private handleAxiosError(error: AxiosError): AppError {
    if (error.response) {
      // 服务器响应了错误状态码
      const { status, data } = error.response;

      switch (status) {
        case 400:
          return new BadRequestError(data.message || 'Bad request');
        case 401:
          return new UnauthorizedError('Authentication required');
        case 403:
          return new ForbiddenError('Access denied');
        case 404:
          return new NotFoundError('Resource not found');
        case 429:
          return new RateLimitError(
            parseInt(error.response.headers['retry-after'])
          );
        case 500:
          return new AppError(503, 'External service unavailable', true);
        default:
          return new AppError(status, data.message || 'Request failed', true);
      }
    } else if (error.request) {
      // 请求已发送但没有收到响应
      return new AppError(
        503,
        'External service not responding',
        true,
        'SERVICE_UNAVAILABLE'
      );
    } else {
      // 请求配置错误
      return new BadRequestError('Invalid request configuration');
    }
  }
}
```

### 重试机制

```typescript
// utils/retry.ts
import { sleep } from './sleep';

export interface RetryOptions {
  maxAttempts?: number;
  delayMs?: number;
  backoffMultiplier?: number;
  maxDelayMs?: number;
  retryableErrors?: string[];
}

export async function retry<T>(
  fn: () => Promise<T>,
  options: RetryOptions = {}
): Promise<T> {
  const {
    maxAttempts = 3,
    delayMs = 1000,
    backoffMultiplier = 2,
    maxDelayMs = 10000,
    retryableErrors = ['ECONNRESET', 'ETIMEDOUT', 'SERVICE_UNAVAILABLE'],
  } = options;

  let lastError: Error;
  let currentDelay = delayMs;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error as Error;

      // 检查是否应该重试
      const isRetryable = retryableErrors.some(code =>
        error instanceof AppError && error.code === code
      ) || error instanceof AppError && error.statusCode >= 500;

      if (!isRetryable || attempt === maxAttempts) {
        throw error;
      }

      // 等待后重试
      console.warn(`Attempt ${attempt} failed, retrying in ${currentDelay}ms...`);
      await sleep(currentDelay);

      // 指数退避
      currentDelay = Math.min(currentDelay * backoffMultiplier, maxDelayMs);
    }
  }

  throw lastError!;
}

// 使用示例
const result = await retry(
  () => externalApiClient.fetchData(),
  {
    maxAttempts: 5,
    delayMs: 1000,
    backoffMultiplier: 2,
  }
);
```

## 前端错误处理

### React Error Boundary

```tsx
// components/ErrorBoundary.tsx
import React, { Component, ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Error caught by boundary:', error, errorInfo);

    // 发送错误到监控服务
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback || (
          <div className="error-boundary">
            <h1>Something went wrong</h1>
            <p>{this.state.error?.message}</p>
            <button onClick={() => window.location.reload()}>
              Reload Page
            </button>
          </div>
        )
      );
    }

    return this.props.children;
  }
}

// 使用
<ErrorBoundary onError={(error, info) => logErrorToService(error, info)}>
  <App />
</ErrorBoundary>
```

### API 错误处理 Hook

```tsx
// hooks/useApiCall.ts
import { useState, useCallback } from 'react';
import { ApiError } from '@/utils/errors';

interface UseApiCallResult<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  execute: () => Promise<void>;
  reset: () => void;
}

export function useApiCall<T>(
  apiFunction: () => Promise<T>
): UseApiCallResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);

  const execute = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const result = await apiFunction();
      setData(result);
    } catch (err) {
      const apiError = err as ApiError;
      setError(apiError);

      // 显示错误提示
      if (apiError.statusCode >= 500) {
        showErrorToast('服务器错误，请稍后重试');
      } else if (apiError.statusCode === 401) {
        showErrorToast('请先登录');
        // 跳转到登录页
        window.location.href = '/login';
      } else if (apiError.message) {
        showErrorToast(apiError.message);
      }
    } finally {
      setLoading(false);
    }
  }, [apiFunction]);

  const reset = useCallback(() => {
    setData(null);
    setError(null);
    setLoading(false);
  }, []);

  return { data, error, loading, execute, reset };
}

// 使用
function UserProfile({ userId }: { userId: string }) {
  const { data: user, error, loading, execute } = useApiCall(
    () => api.users.getById(userId)
  );

  useEffect(() => {
    execute();
  }, [userId]);

  if (loading) return <Spinner />;
  if (error) return <ErrorMessage error={error} />;

  return <div>{user?.name}</div>;
}
```

## 日志和监控

### 错误日志

```typescript
// utils/errorLogger.ts
import { logger } from './logger';

export function logError(error: Error, context?: Record<string, any>) {
  logger.error('Error occurred', {
    message: error.message,
    stack: error.stack,
    name: error.name,
    ...context,
  });

  // 在生产环境发送到监控服务
  if (process.env.NODE_ENV === 'production') {
    sendToSentry(error, context);
  }
}

// Sentry 集成
import * as Sentry from '@sentry/node';

Sentry.init({
  dsn: process.env.SENTRY_DSN,
  environment: process.env.NODE_ENV,
});

function sendToSentry(error: Error, context?: Record<string, any>) {
  Sentry.captureException(error, {
    extra: context,
  });
}
```

### 错误告警

```typescript
// services/alertService.ts
export function alertOnError(error: Error, context?: any) {
  // 关键错误发送告警
  if (isCriticalError(error)) {
    sendAlert({
      level: 'critical',
      message: error.message,
      context,
      channels: ['email', 'slack'],
    });
  }
}

function isCriticalError(error: Error): boolean {
  return (
    error instanceof AppError && error.statusCode >= 500 ||
    error.message.includes('database') ||
    error.message.includes('connection')
  );
}
```

## 错误处理最佳实践

### DO's ✅
- ✅ 使用自定义错误类区分错误类型
- ✅ 在每个层级（Controller、Service、Repository）处理错误
- ✅ 提供清晰的错误消息给客户端
- ✅ 记录详细的错误日志用于调试
- ✅ 对临时性错误实现重试机制
- ✅ 使用 Error Boundary 捕获 React 错误
- ✅ 在生产环境隐藏敏感错误信息

### DON'Ts ❌
- ❌ 不要吞掉错误（空的 catch 块）
- ❌ 不要暴露敏感信息（堆栈、数据库详情）
- ❌ 不要在客户端显示技术性错误消息
- ❌ 不要忽略异步错误
- ❌ 不要在循环中重试而不限制次数
- ❌ 不要混用业务逻辑和错误处理
