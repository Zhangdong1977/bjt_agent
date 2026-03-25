# 测试指南

## 测试策略

### 测试金字塔

```
       E2E Tests (少量)
      /             \
     / Integration  \
    /   Tests (适量)  \
   /__________________\
  /  Unit Tests (大量) \
 /______________________\
```

- **单元测试**：测试独立函数和类
- **集成测试**：测试模块间交互
- **E2E 测试**：测试完整用户流程

## 单元测试

### Jest 配置

```typescript
// jest.config.js
export default {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src', '<rootDir>/test'],
  testMatch: ['**/__tests__/**/*.ts', '**/?(*.)+(spec|test).ts'],
  transform: {
    '^.+\\.ts$': 'ts-jest',
  },
  collectCoverageFrom: [
    'src/**/*.ts',
    '!src/**/*.d.ts',
    '!src/**/index.ts',
  ],
  coverageThreshold: {
    global: {
      branches: 80,
      functions: 80,
      lines: 80,
      statements: 80,
    },
  },
};
```

### 测试工具函数

```typescript
// src/utils/validator.ts
export function validateEmail(email: string): boolean {
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return emailRegex.test(email);
}

export function validatePassword(password: string): {
  valid: boolean;
  errors: string[];
} {
  const errors: string[] = [];

  if (password.length < 8) {
    errors.push('Password must be at least 8 characters');
  }

  if (!/[A-Z]/.test(password)) {
    errors.push('Password must contain uppercase letter');
  }

  if (!/[a-z]/.test(password)) {
    errors.push('Password must contain lowercase letter');
  }

  if (!/[0-9]/.test(password)) {
    errors.push('Password must contain number');
  }

  return {
    valid: errors.length === 0,
    errors,
  };
}

// test/utils/validator.test.ts
import { validateEmail, validatePassword } from '../../src/utils/validator';

describe('validateEmail', () => {
  it('should return true for valid emails', () => {
    expect(validateEmail('test@example.com')).toBe(true);
    expect(validateEmail('user.name+tag@domain.co.uk')).toBe(true);
  });

  it('should return false for invalid emails', () => {
    expect(validateEmail('invalid')).toBe(false);
    expect(validateEmail('test@')).toBe(false);
    expect(validateEmail('@example.com')).toBe(false);
    expect(validateEmail('test..email@example.com')).toBe(false);
  });
});

describe('validatePassword', () => {
  it('should validate strong passwords', () => {
    const result = validatePassword('SecurePass123');
    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
  });

  it('should reject weak passwords', () => {
    const result = validatePassword('weak');
    expect(result.valid).toBe(false);
    expect(result.errors.length).toBeGreaterThan(0);
    expect(result.errors).toContain('Password must be at least 8 characters');
  });
});
```

### 测试异步函数

```typescript
// src/services/userService.ts
export class UserService {
  async findUserById(id: string): Promise<User | null> {
    const user = await database.query('SELECT * FROM users WHERE id = ?', [id]);
    return user;
  }

  async createUser(userData: CreateUserDto): Promise<User> {
    const hashedPassword = await hashPassword(userData.password);
    return database.insert('users', { ...userData, password: hashedPassword });
  }
}

// test/services/userService.test.ts
import { UserService } from '../../src/services/userService';
import { database } from '../mocks/database';

describe('UserService', () => {
  let userService: UserService;

  beforeEach(() => {
    userService = new UserService();
    jest.clearAllMocks();
  });

  describe('findUserById', () => {
    it('should return user when found', async () => {
      const mockUser = { id: '1', name: 'John' };
      database.query.mockResolvedValue(mockUser);

      const result = await userService.findUserById('1');

      expect(result).toEqual(mockUser);
      expect(database.query).toHaveBeenCalledWith('SELECT * FROM users WHERE id = ?', ['1']);
    });

    it('should return null when user not found', async () => {
      database.query.mockResolvedValue(null);

      const result = await userService.findUserById('999');

      expect(result).toBeNull();
    });

    it('should throw database error on failure', async () => {
      database.query.mockRejectedValue(new Error('Database connection failed'));

      await expect(userService.findUserById('1')).rejects.toThrow('Database connection failed');
    });
  });

  describe('createUser', () => {
    it('should create user with hashed password', async () => {
      const userData = { email: 'test@example.com', password: 'password123' };
      const mockUser = { id: '1', ...userData, password: 'hashed_password' };
      database.insert.mockResolvedValue(mockUser);

      const result = await userService.createUser(userData);

      expect(result).toEqual(mockUser);
      expect(database.insert).toHaveBeenCalled();
      expect(result.password).not.toBe(userData.password);
    });
  });
});
```

## Mock 和 Stub

### 使用 Jest Mock

```typescript
// test/mocks/httpClient.mock.ts
import { HttpClient } from '../../src/utils/httpClient';

export const mockHttpClient = {
  get: jest.fn(),
  post: jest.fn(),
  put: jest.fn(),
  delete: jest.fn(),
};

jest.mock('../../src/utils/httpClient', () => ({
  HttpClient: jest.fn(() => mockHttpClient),
}));

// 使用
import { mockHttpClient } from './mocks/httpClient.mock';

describe('External API Service', () => {
  it('should fetch data from external API', async () => {
    const mockData = { results: [{ id: 1, name: 'Test' }] };
    mockHttpClient.get.mockResolvedValue(mockData);

    const service = new ExternalApiService();
    const result = await service.fetchItems();

    expect(result).toEqual(mockData);
    expect(mockHttpClient.get).toHaveBeenCalledWith('https://api.example.com/items');
  });
});
```

### Mock 数据库

```typescript
// test/mocks/database.ts
export const database = {
  query: jest.fn(),
  insert: jest.fn(),
  update: jest.fn(),
  delete: jest.fn(),
  transaction: jest.fn(),
};

export class MockRepository {
  async findOne(options: any): Promise<any> {
    return database.query(options);
  }

  async find(options: any): Promise<any[]> {
    return database.query(options);
  }

  async save(entity: any): Promise<any> {
    return database.insert(entity);
  }
}
```

## 集成测试

### API 集成测试

```typescript
// test/integration/api.test.ts
import request from 'supertest';
import { app } from '../../src/app';
import { database } from '../mocks/database';

describe('POST /api/users', () => {
  beforeAll(async () => {
    // 设置测试数据库
    await database.connect();
  });

  afterAll(async () => {
    await database.disconnect();
  });

  beforeEach(async () => {
    // 清理测试数据
    await database.clear();
  });

  it('should create a new user', async () => {
    const userData = {
      email: 'test@example.com',
      password: 'SecurePass123',
      name: 'Test User',
    };

    const response = await request(app)
      .post('/api/users')
      .send(userData)
      .expect(201);

    expect(response.body).toMatchObject({
      success: true,
      data: {
        email: userData.email,
        name: userData.name,
      },
    });
    expect(response.body.data).not.toHaveProperty('password');
  });

  it('should return validation error for invalid email', async () => {
    const userData = {
      email: 'invalid-email',
      password: 'SecurePass123',
      name: 'Test User',
    };

    const response = await request(app)
      .post('/api/users')
      .send(userData)
      .expect(400);

    expect(response.body).toMatchObject({
      success: false,
      error: {
        code: 'VALIDATION_ERROR',
      },
    });
  });

  it('should return conflict error for duplicate email', async () => {
    const userData = {
      email: 'test@example.com',
      password: 'SecurePass123',
      name: 'Test User',
    };

    // 创建第一个用户
    await request(app).post('/api/users').send(userData);

    // 尝试创建重复邮箱用户
    const response = await request(app)
      .post('/api/users')
      .send(userData)
      .expect(409);

    expect(response.body).toMatchObject({
      success: false,
      error: {
        code: 'CONFLICT',
        message: 'Email already exists',
      },
    });
  });
});
```

### 数据库集成测试

```typescript
// test/integration/database.test.ts
import { AppDataSource } from '../../src/config/database';
import { User } from '../../src/entities/User';
import { UserRepository } from '../../src/repositories/UserRepository';

describe('UserRepository Integration', () => {
  let userRepository: UserRepository;

  beforeAll(async () => {
    // 使用测试数据库
    await AppDataSource.initialize();
    userRepository = new UserRepository();
  });

  afterAll(async () => {
    await AppDataSource.destroy();
  });

  beforeEach(async () => {
    // 清空用户表
    await AppDataSource.getRepository(User).clear();
  });

  it('should create and retrieve user', async () => {
    const userData = {
      email: 'test@example.com',
      password: 'hashed_password',
      name: 'Test User',
    };

    // 创建用户
    const created = await userRepository.create(userData);
    expect(created).toHaveProperty('id');
    expect(created.email).toBe(userData.email);

    // 查询用户
    const found = await userRepository.findById(created.id);
    expect(found).toBeDefined();
    expect(found?.email).toBe(userData.email);
  });

  it('should update user', async () => {
    const user = await userRepository.create({
      email: 'test@example.com',
      password: 'hashed_password',
      name: 'Test User',
    });

    const updated = await userRepository.update(user.id, {
      name: 'Updated Name',
    });

    expect(updated.name).toBe('Updated Name');
  });
});
```

## E2E 测试

### Playwright 配置

```typescript
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
  webServer: {
    command: 'npm run start',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
});
```

### E2E 测试示例

```typescript
// e2e/login.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Authentication', () => {
  test('should login with valid credentials', async ({ page }) => {
    await page.goto('/login');

    await page.fill('input[name="email"]', 'test@example.com');
    await page.fill('input[name="password"]', 'SecurePass123');
    await page.click('button[type="submit"]');

    await expect(page).toHaveURL('/dashboard');
    await expect(page.locator('h1')).toContainText('Welcome');
  });

  test('should show error for invalid credentials', async ({ page }) => {
    await page.goto('/login');

    await page.fill('input[name="email"]', 'test@example.com');
    await page.fill('input[name="password"]', 'wrong_password');
    await page.click('button[type="submit"]');

    await expect(page.locator('.error')).toContainText('Invalid credentials');
  });

  test('should redirect unauthenticated users', async ({ page }) => {
    await page.goto('/dashboard');

    await expect(page).toHaveURL('/login');
  });
});

test.describe('User Management', () => {
  test.beforeEach(async ({ page }) => {
    // 登录
    await page.goto('/login');
    await page.fill('input[name="email"]', 'admin@example.com');
    await page.fill('input[name="password"]', 'AdminPass123');
    await page.click('button[type="submit"]');
    await page.waitForURL('/dashboard');
  });

  test('should create new user', async ({ page }) => {
    await page.goto('/users');
    await page.click('button:has-text("Add User")');

    await page.fill('input[name="name"]', 'John Doe');
    await page.fill('input[name="email"]', 'john@example.com');
    await page.selectOption('select[name="role"]', 'user');
    await page.click('button:has-text("Save")');

    await expect(page.locator('.success')).toContainText('User created successfully');
    await expect(page.locator('table')).toContainText('John Doe');
  });

  test('should delete user', async ({ page }) => {
    await page.goto('/users');

    const userRow = page.locator('tr').filter({ hasText: 'John Doe' });
    await userRow.locator('button:has-text("Delete")').click();
    await page.click('button:has-text("Confirm")');

    await expect(page.locator('.success')).toContainText('User deleted');
  });
});
```

## 测试覆盖率

### 生成覆盖率报告

```bash
# 运行测试并生成覆盖率
npm run test:coverage

# 生成 HTML 覆盖率报告
npm run test:coverage -- --coverage
```

### 覆盖率目标

```typescript
// jest.config.js
coverageThreshold: {
  global: {
    branches: 80,
    functions: 80,
    lines: 80,
    statements: 80,
  },
  './src/core/': {
    branches: 90,
    functions: 90,
    lines: 90,
    statements: 90,
  },
  './src/utils/': {
    branches: 95,
    functions: 95,
    lines: 95,
    statements: 95,
  },
}
```

## CI/CD 集成

### GitHub Actions

```yaml
# .github/workflows/test.yml
name: Test

on:
  push:
    branches: [main, develop]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:14
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test_db
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v3

      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: '20'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Run linter
        run: npm run lint

      - name: Run type check
        run: npm run type-check

      - name: Run unit tests
        run: npm run test:unit -- --coverage

      - name: Run integration tests
        run: npm run test:integration
        env:
          DATABASE_URL: postgresql://test:test@localhost:5432/test_db

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage/lcov.info
```

## 测试最佳实践

### DO's ✅
- ✅ 每个测试只验证一件事
- ✅ 使用描述性的测试名称
- ✅ 遵循 AAA 模式（Arrange, Act, Assert）
- ✅ 测试边界情况
- ✅ 使用 beforeEach/afterEach 清理状态
- ✅ Mock 外部依赖
- ✅ 保持测试独立
- ✅ 使用快照测试时定期更新

### DON'Ts ❌
- ❌ 不要测试第三方库
- ❌ 不要在测试中使用随机数据
- ❌ 不要在测试中硬编码环境相关值
- ❌ 不要忽略测试覆盖率
- ❌ 不要在单元测试中访问真实数据库
- ❌ 不要写脆弱的测试（依赖实现细节）
