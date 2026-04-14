import { test, expect } from '@playwright/test';

const TEST_ACCOUNTS = {
  username: 'zhangdong',
  password: '7745duck',
};

test.describe('标书审查系统 E2E 测试', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
  });

  test.afterEach(async () => {
    // Playwright 会自动清理，无需手动关闭
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