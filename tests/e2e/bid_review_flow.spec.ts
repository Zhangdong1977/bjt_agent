import { test, expect } from '@playwright/test';

const TEST_ACCOUNTS = {
  username: 'zhangdong',
  password: '7745duck',
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
});