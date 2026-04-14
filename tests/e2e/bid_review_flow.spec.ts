import { test, expect, type Page } from '@playwright/test';
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
  let page: Page;

  test.beforeEach(async ({ browser }) => {
    // 连接到已启动的 Chrome（remote debugging）
    const context = await browser.newContext({
      viewport: { width: 1920, height: 1080 },
    });
    page = await context.newPage();
  });

  test.afterEach(async () => {
    await page.close();
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
});