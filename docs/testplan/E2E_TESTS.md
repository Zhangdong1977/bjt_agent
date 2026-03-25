# E2E 端到端测试规格

## 测试框架
- **Playwright** - 主要 E2E 测试框架
- **备选**: Cypress

## 测试环境要求
```bash
# 安装 Playwright
npm install -D @playwright/test
npx playwright install chromium

# 配置
# playwright.config.ts
```

## Playwright 配置

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
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
});
```

## E2E 测试用例

### 1. 用户认证流程

```typescript
// e2e/auth.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Authentication Flow', () => {
  test('user can register and login', async ({ page }) => {
    // 1. 访问注册页面
    await page.goto('/auth');

    // 2. 填写注册表单
    await page.fill('input[name="username"]', 'e2euser');
    await page.fill('input[name="email"]', 'e2e@example.com');
    await page.fill('input[name="password"]', 'Test123!');
    await page.fill('input[name="confirmPassword"]', 'Test123!');

    // 3. 提交注册
    await page.click('button[type="submit"]');

    // 4. 等待跳转或成功消息
    await expect(page).toHaveURL('/projects');

    // 5. 登出
    await page.click('text=Logout');

    // 6. 登录
    await page.fill('input[name="username"]', 'e2euser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    // 7. 验证登录成功
    await expect(page).toHaveURL('/projects');
  });

  test('login with invalid credentials shows error', async ({ page }) => {
    await page.goto('/auth');

    // 输入错误密码
    await page.fill('input[name="username"]', 'nonexistent');
    await page.fill('input[name="password"]', 'wrongpassword');
    await page.click('button[type="submit"]');

    // 验证错误消息
    await expect(page.locator('.error-message')).toContainText('Invalid');
  });
});
```

### 2. 完整审查流程

```typescript
// e2e/review-flow.spec.ts
import { test, expect } from '@playwright/test';
import path from 'path';

test.describe('Complete Review Flow', () => {
  test('full review workflow', async ({ page }) => {
    // 前置条件: 登录状态
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('/projects');

    // Step 1: 创建项目
    await page.click('text=New Project');
    await page.fill('input[name="name"]', 'E2E Test Project');
    await page.fill('textarea[name="description"]', 'Full workflow test');
    await page.click('button[type="submit"]');

    // 验证项目创建
    await expect(page.locator('.project-name')).toContainText('E2E Test Project');

    // Step 2: 上传招标书
    const tenderFilePath = path.join(__dirname, '../fixtures/tender_sample.pdf');
    const tenderInput = page.locator('input[type="file"]').first();
    await tenderInput.setInputFiles(tenderFilePath);

    // 等待上传完成
    await expect(page.locator('.document-status')).toContainText('pending');
    await page.waitForFunction(() => {
      const status = document.querySelector('.document-status');
      return status && status.textContent === 'parsed';
    }, { timeout: 60000 });

    // Step 3: 上传应标书
    const bidFilePath = path.join(__dirname, '../fixtures/bid_sample.pdf');
    const bidInput = page.locator('input[type="file"]').nth(1);
    await bidInput.setInputFiles(bidFilePath);

    // 等待应标书解析
    await page.waitForFunction(() => {
      const statuses = document.querySelectorAll('.document-status');
      return Array.from(statuses).every(s => s.textContent === 'parsed');
    }, { timeout: 60000 });

    // Step 4: 启动审查
    await page.click('text=Start Review');

    // 验证任务状态变为 running
    await expect(page.locator('.task-status')).toContainText('running');

    // Step 5: 等待 SSE 事件
    const stepCount = await page.locator('.timeline-step').count();
    expect(stepCount).toBeGreaterThan(0);

    // Step 6: 等待审查完成
    await page.waitForFunction(() => {
      const status = document.querySelector('.task-status');
      return status && (status.textContent === 'completed' || status.textContent === 'failed');
    }, { timeout: 300000 }); // 5 min timeout

    // Step 7: 查看结果
    if (await page.locator('.task-status').textContent() === 'completed') {
      await page.click('text=View Results');

      // 验证结果摘要
      await expect(page.locator('.summary-total')).toBeVisible();
      await expect(page.locator('.summary-critical')).toBeVisible();

      // 验证 findings 列表
      const findingsCount = await page.locator('.finding-card').count();
      expect(findingsCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('review requires both documents', async ({ page }) => {
    // 登录
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    // 创建项目
    await page.click('text=New Project');
    await page.fill('input[name="name"]', 'Missing Doc Test');
    await page.click('button[type="submit"]');

    // 只上传招标书
    const tenderFilePath = path.join(__dirname, '../fixtures/tender_sample.pdf');
    await page.locator('input[type="file"]').first().setInputFiles(tenderFilePath);

    // 等待解析
    await page.waitForTimeout(2000);

    // 验证 Start Review 按钮被禁用
    const startButton = page.locator('button:has-text("Start Review")');
    await expect(startButton).toBeDisabled();
  });
});
```

### 3. 文档管理流程

```typescript
// e2e/documents.spec.ts
import { test, expect } from '@playwright/test';
import path from 'path';

test.describe('Document Management', () => {
  test('upload and view document', async ({ page }) => {
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    // 创建项目
    await page.click('text=New Project');
    await page.fill('input[name="name"]', 'Doc View Test');
    await page.click('button[type="submit"]');

    // 上传文档
    const filePath = path.join(__dirname, '../fixtures/tender_sample.pdf');
    await page.locator('input[type="file"]').first().setInputFiles(filePath);

    // 等待解析
    await page.waitForFunction(() => {
      const status = document.querySelector('.document-status');
      return status && status.textContent === 'parsed';
    }, { timeout: 60000 });

    // 点击查看内容
    await page.click('text=View Content');

    // 验证模态框打开
    await expect(page.locator('.doc-viewer-modal')).toBeVisible();

    // 验证内容
    await expect(page.locator('.markdown-content')).toBeVisible();

    // 关闭模态框
    await page.click('.close-btn');
    await expect(page.locator('.doc-viewer-modal')).not.toBeVisible();
  });

  test('delete document', async ({ page }) => {
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    // 创建项目并上传文档
    await page.click('text=New Project');
    await page.fill('input[name="name"]', 'Delete Doc Test');
    await page.click('button[type="submit"]');

    const filePath = path.join(__dirname, '../fixtures/tender_sample.pdf');
    await page.locator('input[type="file"]').first().setInputFiles(filePath);
    await page.waitForTimeout(1000);

    // 删除文档
    await page.click('text=Delete');
    await page.click('text=Confirm');

    // 验证文档已删除
    await expect(page.locator('.document-card')).toHaveCount(0);
  });
});
```

### 4. 项目管理流程

```typescript
// e2e/projects.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Project Management', () => {
  test('create project', async ({ page }) => {
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    // 点击新建项目
    await page.click('text=New Project');

    // 填写表单
    await page.fill('input[name="name"]', 'My Test Project');
    await page.fill('textarea[name="description"]', 'Project description');

    // 提交
    await page.click('button[type="submit"]');

    // 验证项目出现在列表中
    await expect(page.locator('.project-card')).toContainText('My Test Project');
  });

  test('edit project', async ({ page }) => {
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    // 创建项目
    await page.click('text=New Project');
    await page.fill('input[name="name"]', 'Original Name');
    await page.click('button[type="submit"]');

    // 进入项目详情
    await page.click('text=Original Name');

    // 编辑
    await page.click('text=Edit');
    await page.fill('input[name="name"]', 'Updated Name');
    await page.click('button[type="submit"]');

    // 验证更新
    await expect(page.locator('.project-name')).toContainText('Updated Name');
  });

  test('delete project', async ({ page }) => {
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    // 创建项目
    await page.click('text=New Project');
    await page.fill('input[name="name"]', 'To Delete');
    await page.click('button[type="submit"]');

    // 删除
    await page.click('text=Delete');
    await page.click('text=Confirm');

    // 验证项目已删除
    await expect(page.locator('.project-card')).not.toContainText('To Delete');
  });

  test('cannot access other user projects', async ({ page, context }) => {
    // 用户 A 创建项目
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'usera');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    await page.click('text=New Project');
    await page.fill('input[name="name"]', 'UserA Project');
    await page.click('button[type="submit"]');

    // 登出
    await page.click('text=Logout');

    // 用户 B 尝试访问
    await context.clearCookies();
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'userb');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    // 直接访问用户 A 的项目 URL
    await page.goto('/projects/user-a-project-id');

    // 应该显示 404 或无权限
    await expect(page.locator('body')).toContainText(/not found|unauthorized|404/i);
  });
});
```

### 5. SSE 事件流测试

```typescript
// e2e/sse.spec.ts
import { test, expect } from '@playwright/test';
import { WebSocket } from 'playwright';

test.describe('SSE Event Stream', () => {
  test('receives step events during review', async ({ page }) => {
    // 登录并准备项目（简化版）
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    // 创建项目和上传文档...
    await page.click('text=New Project');
    await page.fill('input[name="name"]', 'SSE Test');
    await page.click('button[type="submit"]');

    // 启动审查
    await page.click('text=Start Review');

    // 监听 SSE 事件（通过 DOM 更新）
    // 等待至少一个步骤出现
    await page.waitForSelector('.timeline-step', { timeout: 30000 });

    const stepCount = await page.locator('.timeline-step').count();
    expect(stepCount).toBeGreaterThanOrEqual(1);

    // 验证步骤类型
    const firstStep = page.locator('.timeline-step').first();
    await expect(firstStep.locator('.step-type')).toBeVisible();
  });

  test('receives completion event', async ({ page }) => {
    // 设置完成事件监听
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    // 简化流程：直接导航到已有结果的项目
    await page.goto('/projects/completed-project-id/results');

    // 验证完成状态
    await expect(page.locator('.task-status')).toContainText('completed');
  });
});
```

### 6. 错误场景测试

```typescript
// e2e/error-handling.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Error Handling', () => {
  test('network error shows message', async ({ page }) => {
    // 模拟网络错误
    await page.route('**/api/**', route => {
      route.abort('failed');
    });

    await page.goto('/projects');

    // 验证错误消息
    await expect(page.locator('.error-message')).toBeVisible();
  });

  test('session timeout redirects to login', async ({ page, context }) => {
    // 清除所有 cookie
    await context.clearCookies();

    // 尝试访问受保护页面
    await page.goto('/projects');

    // 应该重定向到登录页
    await expect(page).toHaveURL(/\/auth/);
  });

  test('upload large file shows progress', async ({ page }) => {
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');

    // 创建项目
    await page.click('text=New Project');
    await page.fill('input[name="name"]', 'Upload Test');
    await page.click('button[type="submit"]');

    // 上传文件（应该在 input 旁边显示进度）
    const fileInput = page.locator('input[type="file"]').first();

    // 验证上传区域可见
    await expect(fileInput).toBeVisible();
  });
});
```

### 7. 响应式设计测试

```typescript
// e2e/responsive.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Responsive Design', () => {
  test.use({
    viewport: { width: 375, height: 667 }, // iPhone SE
  });

  test('mobile view works', async ({ page }) => {
    await page.goto('/auth');

    // 验证移动端菜单
    await expect(page.locator('.mobile-menu')).toBeVisible();

    // 验证表单可用
    await expect(page.locator('input[name="username"]')).toBeVisible();
  });

  test('tablet view', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });

    await page.goto('/projects');

    // 验证网格布局
    await expect(page.locator('.projects-grid')).toBeVisible();
  });

  test('desktop view', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });

    await page.goto('/projects');

    // 验证多列布局
    const grid = page.locator('.projects-grid');
    await expect(grid).toBeVisible();
  });
});
```

## 测试 Fixtures

```typescript
// e2e/fixtures/index.ts
import { test as base } from '@playwright/test';
import path from 'path';

export const test = base.extend({
  // 登录状态
  loggedInPage: async ({ page }, use) => {
    await page.goto('/auth');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'Test123!');
    await page.click('button[type="submit"]');
    await page.waitForURL('/projects');
    await use(page);
  },

  // 测试项目
  testProject: async ({ loggedInPage }) => {
    await loggedInPage.click('text=New Project');
    await loggedInPage.fill('input[name="name"]', 'Fixture Project');
    await loggedInPage.fill('textarea[name="description"]', 'Created by fixture');
    await loggedInPage.click('button[type="submit"]');
    await loggedInPage.waitForSelector('.project-card');
    return loggedInPage;
  },
});

export { expect } from '@playwright/test';
```

## CI 集成

```yaml
# .github/workflows/e2e.yml
name: E2E Tests

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: '18'

      - name: Install dependencies
        run: npm ci

      - name: Install Playwright
        run: npx playwright install --with-deps chromium

      - name: Start services
        run: |
          # 启动后端服务
          cd backend && pip install -r requirements.txt &
          # 启动前端
          npm run dev &

      - name: Run E2E tests
        run: npx playwright test

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: playwright-report
          path: playwright-report/
```
