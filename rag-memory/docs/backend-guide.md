# 后端开发指南

## 技术栈

系统后端采用以下技术栈：
- Node.js 20 LTS
- TypeScript 5
- Express.js 框架
- TypeORM 数据库ORM
- JWT 认证
- Redis 缓存
- Bull 任务队列
- Winston 日志

## 项目结构

```
src/
├── controllers/      # 控制器层
├── services/         # 业务逻辑层
├── repositories/     # 数据访问层
├── entities/         # 数据库实体
├── middlewares/      # 中间件
├── routes/           # 路由定义
├── dto/              # 数据传输对象
├── utils/            # 工具函数
├── config/           # 配置文件
└── app.ts            # 应用入口
```

## 控制器模式

### 基础控制器

```typescript
// controllers/UserController.ts
import { Request, Response, NextFunction } from 'express';
import { UserService } from '../services/UserService';
import { CreateUserDto, UpdateUserDto } from '../dto/UserDto';
import { validationMiddleware } from '../middlewares/validationMiddleware';

export class UserController {
  private userService = new UserService();

  // 获取用户列表
  async getUsers(req: Request, res: Response, next: NextFunction) {
    try {
      const page = parseInt(req.query.page as string) || 1;
      const limit = parseInt(req.query.limit as string) || 10;
      const result = await this.userService.getUsers(page, limit);
      res.json({
        success: true,
        data: result.data,
        meta: { total: result.total, page, limit },
      });
    } catch (error) {
      next(error);
    }
  }

  // 获取单个用户
  async getUserById(req: Request, res: Response, next: NextFunction) {
    try {
      const { id } = req.params;
      const user = await this.userService.getUserById(id);
      res.json({ success: true, data: user });
    } catch (error) {
      next(error);
    }
  }

  // 创建用户
  async createUser(req: Request, res: Response, next: NextFunction) {
    try {
      const createUserDto: CreateUserDto = req.body;
      const user = await this.userService.createUser(createUserDto);
      res.status(201).json({ success: true, data: user });
    } catch (error) {
      next(error);
    }
  }

  // 更新用户
  async updateUser(req: Request, res: Response, next: NextFunction) {
    try {
      const { id } = req.params;
      const updateUserDto: UpdateUserDto = req.body;
      const user = await this.userService.updateUser(id, updateUserDto);
      res.json({ success: true, data: user });
    } catch (error) {
      next(error);
    }
  }

  // 删除用户
  async deleteUser(req: Request, res: Response, next: NextFunction) {
    try {
      const { id } = req.params;
      await this.userService.deleteUser(id);
      res.status(204).send();
    } catch (error) {
      next(error);
    }
  }
}
```

### 路由配置

```typescript
// routes/userRoutes.ts
import { Router } from 'express';
import { UserController } from '../controllers/UserController';
import { authMiddleware } from '../middlewares/authMiddleware';
import { validationMiddleware } from '../middlewares/validationMiddleware';
import { CreateUserDto, UpdateUserDto } from '../dto/UserDto';

const router = Router();
const userController = new UserController();

router.get('/', userController.getUsers.bind(userController));
router.get(
  '/:id',
  authMiddleware,
  userController.getUserById.bind(userController)
);
router.post(
  '/',
  validationMiddleware(CreateUserDto),
  userController.createUser.bind(userController)
);
router.put(
  '/:id',
  authMiddleware,
  validationMiddleware(UpdateUserDto),
  userController.updateUser.bind(userController)
);
router.delete(
  '/:id',
  authMiddleware,
  userController.deleteUser.bind(userController)
);

export default router;
```

## 服务层

### 业务逻辑封装

```typescript
// services/UserService.ts
import { Repository } from 'typeorm';
import { AppDataSource } from '../config/database';
import { User } from '../entities/User';
import { CreateUserDto } from '../dto/UserDto';
import { hashPassword, comparePassword } from '../utils/crypto';
import { generateToken } from '../utils/jwt';

export class UserService {
  private userRepository: Repository<User>;

  constructor() {
    this.userRepository = AppDataSource.getRepository(User);
  }

  async getUsers(page: number, limit: number) {
    const [data, total] = await this.userRepository.findAndCount({
      skip: (page - 1) * limit,
      take: limit,
      select: ['id', 'email', 'name', 'role', 'createdAt'],
    });
    return { data, total };
  }

  async getUserById(id: string) {
    const user = await this.userRepository.findOne({
      where: { id },
      select: ['id', 'email', 'name', 'role', 'createdAt'],
    });

    if (!user) {
      throw new NotFoundError('User not found');
    }

    return user;
  }

  async createUser(dto: CreateUserDto) {
    // 检查邮箱是否已存在
    const existingUser = await this.userRepository.findOne({
      where: { email: dto.email },
    });

    if (existingUser) {
      throw new ConflictError('Email already exists');
    }

    // 创建新用户
    const hashedPassword = await hashPassword(dto.password);
    const user = this.userRepository.create({
      ...dto,
      password: hashedPassword,
    });

    await this.userRepository.save(user);

    // 返回用户信息（不包含密码）
    const { password, ...userWithoutPassword } = user;
    return userWithoutPassword;
  }

  async loginUser(email: string, password: string) {
    const user = await this.userRepository.findOne({
      where: { email },
    });

    if (!user) {
      throw new UnauthorizedError('Invalid credentials');
    }

    const isPasswordValid = await comparePassword(password, user.password);

    if (!isPasswordValid) {
      throw new UnauthorizedError('Invalid credentials');
    }

    const token = generateToken({ userId: user.id, role: user.role });
    return { token, user: { id: user.id, email: user.email, name: user.name } };
  }
}
```

## 中间件

### 认证中间件

```typescript
// middlewares/authMiddleware.ts
import { Request, Response, NextFunction } from 'express';
import { verifyToken } from '../utils/jwt';

export interface AuthRequest extends Request {
  userId?: string;
  userRole?: string;
}

export async function authMiddleware(
  req: AuthRequest,
  res: Response,
  next: NextFunction
) {
  try {
    const authHeader = req.headers.authorization;

    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      return res.status(401).json({ error: 'No token provided' });
    }

    const token = authHeader.substring(7);
    const decoded = verifyToken(token);

    req.userId = decoded.userId;
    req.userRole = decoded.role;

    next();
  } catch (error) {
    res.status(401).json({ error: 'Invalid token' });
  }
}

export function requireRole(...roles: string[]) {
  return (req: AuthRequest, res: Response, next: NextFunction) => {
    if (!req.userRole || !roles.includes(req.userRole)) {
      return res.status(403).json({ error: 'Insufficient permissions' });
    }
    next();
  };
}
```

### 错误处理中间件

```typescript
// middlewares/errorMiddleware.ts
import { Request, Response, NextFunction } from 'express';
import { ZodError } from 'zod';

export class AppError extends Error {
  constructor(
    public statusCode: number,
    public message: string,
    public isOperational = true
  ) {
    super(message);
    Object.setPrototypeOf(this, AppError.prototype);
  }
}

export function errorHandler(
  error: Error,
  req: Request,
  res: Response,
  next: NextFunction
) {
  console.error('Error:', error);

  if (error instanceof ZodError) {
    return res.status(400).json({
      success: false,
      error: {
        code: 'VALIDATION_ERROR',
        message: 'Validation failed',
        details: error.errors,
      },
    });
  }

  if (error instanceof AppError) {
    return res.status(error.statusCode).json({
      success: false,
      error: {
        code: error.message,
        message: error.message,
      },
    });
  }

  res.status(500).json({
    success: false,
    error: {
      code: 'INTERNAL_SERVER_ERROR',
      message: 'An unexpected error occurred',
    },
  });
}
```

### 验证中间件

```typescript
// middlewares/validationMiddleware.ts
import { Request, Response, NextFunction } from 'express';
import { AnyZodObject, ZodError } from 'zod';

export function validationMiddleware(schema: AnyZodObject) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      await schema.parseAsync({
        body: req.body,
        query: req.query,
        params: req.params,
      });
      next();
    } catch (error) {
      if (error instanceof ZodError) {
        res.status(400).json({
          success: false,
          error: {
            code: 'VALIDATION_ERROR',
            details: error.errors,
          },
        });
      } else {
        next(error);
      }
    }
  };
}
```

## DTO 验证

```typescript
// dto/UserDto.ts
import { z } from 'zod';

export const CreateUserDtoSchema = z.object({
  email: z.string().email('Invalid email format'),
  password: z.string().min(8, 'Password must be at least 8 characters'),
  name: z.string().min(2, 'Name must be at least 2 characters'),
  role: z.enum(['user', 'admin', 'moderator']).default('user'),
});

export type CreateUserDto = z.infer<typeof CreateUserDtoSchema>;

export const UpdateUserDtoSchema = CreateUserDtoSchema.partial().extend({
  email: z.string().email().optional(),
});

export type UpdateUserDto = z.infer<typeof UpdateUserDtoSchema>;

export const LoginDtoSchema = z.object({
  email: z.string().email('Invalid email format'),
  password: z.string().min(1, 'Password is required'),
});

export type LoginDto = z.infer<typeof LoginDtoSchema>;
```

## 任务队列

```typescript
// queues/emailQueue.ts
import Queue from 'bull';
import { sendEmail } from '../services/emailService';

export const emailQueue = new Queue('email', {
  redis: {
    host: process.env.REDIS_HOST,
    port: parseInt(process.env.REDIS_PORT || '6379'),
  },
});

emailQueue.process(async (job) => {
  const { to, subject, body } = job.data;
  await sendEmail(to, subject, body);
});

// 添加任务
export async function sendWelcomeEmail(userId: string) {
  await emailQueue.add({
    to: userId,
    subject: 'Welcome to our platform',
    body: 'Thank you for registering!',
  });
}
```

## 缓存策略

```typescript
// utils/cache.ts
import Redis from 'ioredis';

const redis = new Redis({
  host: process.env.REDIS_HOST,
  port: parseInt(process.env.REDIS_PORT || '6379'),
});

export async function getCache<T>(key: string): Promise<T | null> {
  const cached = await redis.get(key);
  return cached ? JSON.parse(cached) : null;
}

export async function setCache<T>(
  key: string,
  value: T,
  ttl: number = 3600
): Promise<void> {
  await redis.setex(key, ttl, JSON.stringify(value));
}

export async function deleteCache(key: string): Promise<void> {
  await redis.del(key);
}

// 使用示例
export class PostService {
  async getPopularPosts() {
    const cacheKey = 'posts:popular';

    // 先查缓存
    const cached = await getCache<Post[]>(cacheKey);
    if (cached) return cached;

    // 缓存未命中，查数据库
    const posts = await this.postRepository.find({
      order: { views: 'DESC' },
      take: 10,
    });

    // 写入缓存
    await setCache(cacheKey, posts, 600); // 10分钟

    return posts;
  }
}
```

## 日志系统

```typescript
// utils/logger.ts
import winston from 'winston';

export const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: winston.format.combine(
    winston.format.timestamp(),
    winston.format.errors({ stack: true }),
    winston.format.splat(),
    winston.format.json()
  ),
  transports: [
    new winston.transports.File({ filename: 'logs/error.log', level: 'error' }),
    new winston.transports.File({ filename: 'logs/combined.log' }),
  ],
});

if (process.env.NODE_ENV !== 'production') {
  logger.add(
    new winston.transports.Console({
      format: winston.format.combine(
        winston.format.colorize(),
        winston.format.simple()
      ),
    })
  );
}

// 使用示例
logger.info('User logged in', { userId: '123', ip: '192.168.1.1' });
logger.error('Database connection failed', { error: err.message });
```

## 应用启动

```typescript
// app.ts
import express from 'express';
import { AppDataSource } from './config/database';
import { errorHandler } from './middlewares/errorMiddleware';
import userRoutes from './routes/userRoutes';
import postRoutes from './routes/postRoutes';

const app = express();

app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// 路由
app.use('/api/users', userRoutes);
app.use('/api/posts', postRoutes);

// 错误处理
app.use(errorHandler);

// 初始化数据库并启动服务器
async function start() {
  try {
    await AppDataSource.initialize();
    console.log('Database connected');

    const PORT = process.env.PORT || 3000;
    app.listen(PORT, () => {
      console.log(`Server running on port ${PORT}`);
    });
  } catch (error) {
    console.error('Failed to start server:', error);
    process.exit(1);
  }
}

start();
```
