import { test, expect } from '@playwright/test';
import path from 'path';

const TEST_ACCOUNTS = {
  username: 'zhangdong',
  password: '7745duck',
};

const TEST_DOCUMENTS = {
  tender: path.resolve(__dirname, '../../testdocuments/迪维勒普招标文件.docx'),
  bid: path.resolve(__dirname, '../../testdocuments/迪维勒普投标文件.docx'),
};

test.describe('标书审查系统 E2E 测试', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
  });

  test('步骤1: 登录页面显示正确', async ({ page }) => {
    await page.goto('/login');
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('#username')).toBeVisible();
    await expect(page.locator('#password')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test('步骤2: 登录成功并跳转到首页', async ({ page }) => {
    await page.goto('/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home\/check/, { timeout: 10000 });
  });

  test('步骤3: 创建新项目', async ({ page }) => {
    // 先登录
    await page.goto('/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home\/check/, { timeout: 10000 });

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
    await page.goto('/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home\/check/, { timeout: 10000 });

    const projectName = `测试项目_${Date.now()}`;
    await page.fill('input[placeholder="请输入项目名称"]', projectName);
    await page.click('.ant-btn-primary');
    await expect(page).toHaveURL(/\/projects\/[a-f0-9-]+/, { timeout: 10000 });

    // 上传招标书
    const tenderInput = page.locator('#tender-upload');
    await tenderInput.setInputFiles(TEST_DOCUMENTS.tender);

    // 等待文件显示
    await expect(page.locator('.filename').first()).toContainText('迪维勒普招标文件', { timeout: 5000 });

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
    await page.goto('/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home\/check/, { timeout: 10000 });

    const projectName = `测试项目_${Date.now()}`;
    await page.fill('input[placeholder="请输入项目名称"]', projectName);
    await page.click('.ant-btn-primary');
    await expect(page).toHaveURL(/\/projects\/[a-f0-9-]+/, { timeout: 10000 });

    // 上传应标书
    const bidInput = page.locator('#bid-upload');
    await bidInput.setInputFiles(TEST_DOCUMENTS.bid);

    // 等待文件显示
    await expect(page.locator('.filename').first()).toContainText('迪维勒普投标文件', { timeout: 5000 });

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
    await page.goto('/login');
    await page.fill('#username', TEST_ACCOUNTS.username);
    await page.fill('#password', TEST_ACCOUNTS.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/home\/check/, { timeout: 10000 });

    // 2. 创建项目
    const projectName = `测试项目_${Date.now()}`;
    await page.fill('input[placeholder="请输入项目名称"]', projectName);
    await page.click('.ant-btn-primary');
    await expect(page).toHaveURL(/\/projects\/[a-f0-9-]+/, { timeout: 10000 });

    // 3. 上传招标书
    const tenderInput = page.locator('#tender-upload');
    await tenderInput.setInputFiles(TEST_DOCUMENTS.tender);
    await expect(page.locator('.filename').first()).toContainText('迪维勒普招标文件', { timeout: 5000 });

    // 4. 上传应标书
    const bidInput = page.locator('#bid-upload');
    await bidInput.setInputFiles(TEST_DOCUMENTS.bid);
    await expect(page.locator('.filename').nth(1)).toContainText('迪维勒普投标文件', { timeout: 5000 });

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
});