import { test, expect } from '@playwright/test';
import path from 'path';

const TEST_ACCOUNTS = {
  username: 'zhangdong',
  password: '7745duck',
};

const TEST_DOCUMENTS = {
  tender: path.resolve(__dirname, '../../testdocuments/招标文件.docx'),
  bid: path.resolve(__dirname, '../../testdocuments/投标文件.docx'),
};

test.describe('标书审查系统 E2E 测试', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
  });

  test('步骤1: 登录页面显示正确', async ({ page }) => {
    await page.goto('http://localhost:3000/login');
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('#username')).toBeVisible();
    await expect(page.locator('#password')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test('步骤2: 登录成功并跳转到首页', async ({ page }) => {
    await page.goto('http://localhost:3000/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home/, { timeout: 10000 });
  });

  test('步骤3: 创建新项目', async ({ page }) => {
    // 先登录
    await page.goto('http://localhost:3000/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home/, { timeout: 10000 });

    // 创建项目
    const projectName = `测试项目_${Date.now()}`;
    await page.fill('input[placeholder="请输入项目名称"]', projectName);
    await page.click('.ant-btn-primary');

    // 等待跳转到项目页面
    await expect(page).toHaveURL(/\/projects\/[a-f0-9-]+/, { timeout: 10000 });
    await expect(page.locator('.start-review-btn')).toBeVisible();
  });

  test('步骤4: 上传招标书并等待解析完成', async ({ page }) => {
    // 登录并创建项目
    await page.goto('http://localhost:3000/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home/, { timeout: 10000 });

    const projectName = `测试项目_${Date.now()}`;
    await page.fill('input[placeholder="请输入项目名称"]', projectName);
    await page.click('.ant-btn-primary');
    await expect(page).toHaveURL(/\/projects\/[a-f0-9-]+/, { timeout: 10000 });

    // 上传招标书
    const tenderInput = page.locator('#tender-upload');
    await tenderInput.setInputFiles(TEST_DOCUMENTS.tender);

    // 等待文件显示
    await expect(page.locator('.filename').first()).toContainText('招标文件', { timeout: 5000 });

    // 等待解析完成（最多60秒）
    await page.waitForFunction(() => {
      const statusElements = document.querySelectorAll('.status');
      for (const el of statusElements) {
        if (el.textContent?.trim() === 'parsed') {
          return true;
        }
      }
      return false;
    }, { timeout: 60000 });
  });

  test('步骤5: 上传应标书并等待解析完成', async ({ page }) => {
    // 登录并创建项目
    await page.goto('http://localhost:3000/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home/, { timeout: 10000 });

    const projectName = `测试项目_${Date.now()}`;
    await page.fill('input[placeholder="请输入项目名称"]', projectName);
    await page.click('.ant-btn-primary');
    await expect(page).toHaveURL(/\/projects\/[a-f0-9-]+/, { timeout: 10000 });

    // 上传应标书
    const bidInput = page.locator('#bid-upload');
    await bidInput.setInputFiles(TEST_DOCUMENTS.bid);

    // 等待文件显示
    await expect(page.locator('.filename').first()).toContainText('投标文件', { timeout: 5000 });

    // 等待解析完成（最多60秒）
    await page.waitForFunction(() => {
      const statusElements = document.querySelectorAll('.status');
      for (const el of statusElements) {
        if (el.textContent?.trim() === 'parsed') {
          return true;
        }
      }
      return false;
    }, { timeout: 60000 });
  });

  test('完整流程: 登录 -> 创建项目 -> 上传文档 -> 启动审查 -> 验证审查详情页', async ({ page }) => {
    // 1. 登录
    await page.goto('http://localhost:3000/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home/, { timeout: 10000 });

    // 2. 创建项目
    const projectName = `测试项目_${Date.now()}`;
    await page.fill('input[placeholder="请输入项目名称"]', projectName);
    await page.click('.ant-btn-primary');
    await expect(page).toHaveURL(/\/projects\/[a-f0-9-]+/, { timeout: 10000 });

    // 3. 上传招标书
    const tenderInput = page.locator('#tender-upload');
    await tenderInput.setInputFiles(TEST_DOCUMENTS.tender);
    await expect(page.locator('.filename').first()).toContainText('招标文件', { timeout: 5000 });

    // 4. 上传应标书
    const bidInput = page.locator('#bid-upload');
    await bidInput.setInputFiles(TEST_DOCUMENTS.bid);
    await expect(page.locator('.filename').nth(1)).toContainText('投标文件', { timeout: 5000 });

    // 5. 等待两个文档都解析完成
    await page.waitForFunction(() => {
      const statusElements = document.querySelectorAll('.status');
      if (statusElements.length >= 2) {
        return Array.from(statusElements).every(el => el.textContent?.trim() === 'parsed');
      }
      return false;
    }, { timeout: 120000 });

    // 6. 点击开始审查
    const startButton = page.locator('.start-review-btn');
    await expect(startButton).toBeEnabled();
    await startButton.click();

    // 7. 验证跳转到审查详情页
    await expect(page).toHaveURL(/\/review-execution/, { timeout: 10000 });

    // 8. 验证 LeftPane 组件存在
    await expect(page.locator('.left-pane')).toBeVisible({ timeout: 10000 });

    // 9. 验证页面包含必要的组件
    await expect(page.locator('.phase-block')).toBeVisible({ timeout: 10000 });
  });

  test('步骤6: 验证审查页面头部组件显示正确', async ({ page }) => {
    // 登录并创建项目，上传文档，启动审查
    await page.goto('http://localhost:3000/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home/, { timeout: 10000 });

    const projectName = `测试项目_${Date.now()}`;
    await page.fill('input[placeholder="请输入项目名称"]', projectName);
    await page.click('.ant-btn-primary');
    await expect(page).toHaveURL(/\/projects\/[a-f0-9-]+/, { timeout: 10000 });

    const tenderInput = page.locator('#tender-upload');
    await tenderInput.setInputFiles(TEST_DOCUMENTS.tender);
    const bidInput = page.locator('#bid-upload');
    await bidInput.setInputFiles(TEST_DOCUMENTS.bid);

    await page.waitForFunction(() => {
      const statusElements = document.querySelectorAll('.status');
      if (statusElements.length >= 2) {
        return Array.from(statusElements).every(el => el.textContent?.trim() === 'parsed');
      }
      return false;
    }, { timeout: 120000 });

    await page.locator('.start-review-btn').click();
    await expect(page).toHaveURL(/\/review-execution/, { timeout: 10000 });

    // 验证 ExecutionHeader 组件
    await expect(page.locator('.execution-header')).toBeVisible();
    await expect(page.locator('.project-title')).toBeVisible();
    await expect(page.locator('.back-btn')).toBeVisible();
    await expect(page.locator('.status-badge')).toBeVisible();
  });

  test('步骤7: 验证执行步骤指示器(Stepper)组件', async ({ page }) => {
    // 登录并启动审查
    await page.goto('http://localhost:3000/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home/, { timeout: 10000 });

    const projectName = `测试项目_${Date.now()}`;
    await page.fill('input[placeholder="请输入项目名称"]', projectName);
    await page.click('.ant-btn-primary');
    await expect(page).toHaveURL(/\/projects\/[a-f0-9-]+/, { timeout: 10000 });

    const tenderInput = page.locator('#tender-upload');
    await tenderInput.setInputFiles(TEST_DOCUMENTS.tender);
    const bidInput = page.locator('#bid-upload');
    await bidInput.setInputFiles(TEST_DOCUMENTS.bid);

    await page.waitForFunction(() => {
      const statusElements = document.querySelectorAll('.status');
      if (statusElements.length >= 2) {
        return Array.from(statusElements).every(el => el.textContent?.trim() === 'parsed');
      }
      return false;
    }, { timeout: 120000 });

    await page.locator('.start-review-btn').click();
    await expect(page).toHaveURL(/\/review-execution/, { timeout: 10000 });

    // 验证 Stepper 组件存在
    await expect(page.locator('.stepper')).toBeVisible();

    // 验证至少有一个步骤显示
    const steps = page.locator('.step');
    const stepCount = await steps.count();
    expect(stepCount).toBeGreaterThanOrEqual(1);

    // 验证步骤标签可见
    await expect(page.locator('.step-label').first()).toBeVisible();
  });

  test('步骤8: 验证左侧面板(LeftPane)内容区域', async ({ page }) => {
    // 登录并启动审查
    await page.goto('http://localhost:3000/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home/, { timeout: 10000 });

    const projectName = `测试项目_${Date.now()}`;
    await page.fill('input[placeholder="请输入项目名称"]', projectName);
    await page.click('.ant-btn-primary');
    await expect(page).toHaveURL(/\/projects\/[a-f0-9-]+/, { timeout: 10000 });

    const tenderInput = page.locator('#tender-upload');
    await tenderInput.setInputFiles(TEST_DOCUMENTS.tender);
    const bidInput = page.locator('#bid-upload');
    await bidInput.setInputFiles(TEST_DOCUMENTS.bid);

    await page.waitForFunction(() => {
      const statusElements = document.querySelectorAll('.status');
      if (statusElements.length >= 2) {
        return Array.from(statusElements).every(el => el.textContent?.trim() === 'parsed');
      }
      return false;
    }, { timeout: 120000 });

    await page.locator('.start-review-btn').click();
    await expect(page).toHaveURL(/\/review-execution/, { timeout: 10000 });

    // 验证 LeftPane 容器
    await expect(page.locator('.left-pane')).toBeVisible();

    // 验证左侧面板内至少有一个内容区域可见
    const leftPane = page.locator('.left-pane');
    await expect(leftPane).toBeVisible();

    // 检查 phase-block 是否存在
    const phaseBlockCount = await page.locator('.phase-block').count();
    if (phaseBlockCount > 0) {
      await expect(page.locator('.phase-block').first()).toBeVisible();
      await expect(page.locator('.phase-label').first()).toBeVisible();
    }

    // 检查 output-block 是否存在
    const outputBlockCount = await page.locator('.output-block').count();
    if (outputBlockCount > 0) {
      await expect(page.locator('.output-block').first()).toBeVisible();
      await expect(page.locator('.output-title')).toBeVisible();
      await expect(page.locator('.wait-icon')).toBeVisible();
    }
  });

  test('步骤9: 验证右侧边栏(RightSidebar)统计信息', async ({ page }) => {
    // 登录并启动审查
    await page.goto('http://localhost:3000/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home/, { timeout: 10000 });

    const projectName = `测试项目_${Date.now()}`;
    await page.fill('input[placeholder="请输入项目名称"]', projectName);
    await page.click('.ant-btn-primary');
    await expect(page).toHaveURL(/\/projects\/[a-f0-9-]+/, { timeout: 10000 });

    const tenderInput = page.locator('#tender-upload');
    await tenderInput.setInputFiles(TEST_DOCUMENTS.tender);
    const bidInput = page.locator('#bid-upload');
    await bidInput.setInputFiles(TEST_DOCUMENTS.bid);

    await page.waitForFunction(() => {
      const statusElements = document.querySelectorAll('.status');
      if (statusElements.length >= 2) {
        return Array.from(statusElements).every(el => el.textContent?.trim() === 'parsed');
      }
      return false;
    }, { timeout: 120000 });

    await page.locator('.start-review-btn').click();
    await expect(page).toHaveURL(/\/review-execution/, { timeout: 10000 });

    // 验证 RightSidebar 组件
    await expect(page.locator('.right-sidebar')).toBeVisible();

    // 验证侧边栏各区域
    await expect(page.locator('.sidebar-section').first()).toBeVisible();

    // 验证状态指示器
    await expect(page.locator('.status-indicator')).toBeVisible();
    await expect(page.locator('.status-dot')).toBeVisible();
    await expect(page.locator('.status-text')).toBeVisible();

    // 验证进度条
    await expect(page.locator('.overall-progress')).toBeVisible();
    await expect(page.locator('.progress-bar-outer')).toBeVisible();

    // 验证统计网格存在
    await expect(page.locator('.stats-grid')).toBeVisible();

    // 验证图例列表
    await expect(page.locator('.legend-list')).toBeVisible();
    const legendItems = page.locator('.leg');
    const legendCount = await legendItems.count();
    expect(legendCount).toBeGreaterThanOrEqual(1);

    // 验证操作按钮区域
    await expect(page.locator('.actions')).toBeVisible();
  });
});