# E2E 测试用例设计：标书审查系统

## 目标

使用 Playwright 对标书审查系统进行端到端测试，验证完整流程：登录 → 创建项目 → 上传文档 → 等待解析 → 启动审查 → 进入审查详情页并验证各组件显示正确。

## 技术栈

- **测试框架**: Playwright（已安装在 `frontend/node_modules/@playwright/test`）
- **浏览器**: Chrome（非无头模式，连接到 `localhost:9222`）
- **显示环境**: VNC DISPLAY=:2
- **测试环境**: py311 conda 环境
- **测试数据**: `testdocuments/迪维勒普招标文件.docx` 和 `testdocuments/迪维勒普投标文件.docx`

## 测试结构

```
tests/e2e/
└── bid_review_flow.spec.ts   # 主测试文件
```

## 测试用例

### 测试账户

- 用户名: `zhangdong`
- 密码: `7745duck`

### 测试步骤

| 步骤 | 操作 | 验证点 |
|------|------|--------|
| 1 | 访问登录页面 `http://localhost:3000/login` | URL 包含 `/login`，显示用户名/密码输入框和登录按钮 |
| 2 | 填写登录表单并提交 | 成功跳转至 `/home/check` |
| 3 | 创建新项目（项目名称使用时间戳生成唯一值） | 跳转至 `/projects/{id}`，页面显示文档上传区域 |
| 4 | 上传招标书 `testdocuments/迪维勒普招标文件.docx` | 文件上传成功，显示文件名，状态变为 `parsed` |
| 5 | 上传应标书 `testdocuments/迪维勒普投标文件.docx` | 文件上传成功，显示文件名，状态变为 `parsed` |
| 6 | 等待两个文档解析完成（最多 60 秒轮询） | 两个文档状态都为 `parsed` |
| 7 | 点击"开始审查"按钮 | 跳转至 `/projects/{id}/review-execution` |
| 8 | 验证审查详情页 LeftPane 组件 | 验证待办任务列表、主代理输出、子代理时间线、合并阶段等显示正确 |

### 关键选择器

```typescript
// 登录页面
'#username'           // 用户名输入框
'#password'           // 密码输入框
'button[type="submit"]' // 登录按钮

// CheckView - 创建项目
'input[placeholder="请输入项目名称"]' // 项目名称输入框
'.ant-btn-primary'    // 创建按钮

// ProjectView - 文档上传
'#tender-upload'      // 招标书文件输入
'#bid-upload'         // 应标书文件输入
'.start-review-btn'   // 开始审查按钮
'.status'             // 文档状态显示

// ReviewExecutionView - 审查详情页
'.left-pane'          // LeftPane 组件容器
'.phase-block'        // 阶段块
'.master-timeline'    // 主代理时间线
'.merge-block'        // 合并阶段块
```

### 验收条款

进入审查详情页面 `review-execution` 后，验证 `LeftPane.vue` 组件：

1. **待办任务列表** (`SubAgentExecutorBlock`)
   - 显示子代理及其检查项状态
   - 状态包括: pending, running, completed, failed

2. **主代理输出** (`AgentTimelineItem`)
   - MasterAgent 时间线正确渲染
   - 显示 step_number, step_type, content

3. **子代理时间线** (`SubAgentExecutorBlock`)
   - SubAgentExecutorBlock 正确显示
   - 每个子代理的 steps 正确映射

4. **合并阶段** (`merge-block`)
   - phase 为 `completed` 时显示合并统计信息
   - 显示: 汇总子代理结果、去重与标准化、优先级排序、异常二次校验、生成审查报告

### 等待策略

```typescript
// 文档解析状态轮询（最多 60 秒）
await expect(page.locator('.status')).toHaveText('parsed', { timeout: 60000 });

// 审查页面加载
await expect(page).toHaveURL(/\/review-execution/);
```

### 错误处理

- **文档解析超时**: 60 秒内未解析完成则测试失败
- **审查启动失败**: 检查 errorMessage 是否显示
- **网络请求**: 使用 Playwright 默认重试机制

### 非无头模式配置

```typescript
// playwright.config.ts
export default defineConfig({
  use: {
    browserName: 'chromium',
    launchOptions: {
      executablePath: '/usr/bin/google-chrome',
      args: [
        '--remote-debugging-port=9222',
        '--no-sandbox',
        '--disable-dev-shm-usage'
      ]
    }
  }
});
```

### 运行测试

```bash
# 在 frontend 目录下运行
cd frontend
npx playwright test --config=./playwright.config.ts tests/e2e/bid_review_flow.spec.ts
```

### 测试后清理

- **不清理** - 保留创建的项目和文档，供人工检查和调试

## 文件清单

| 文件 | 描述 |
|------|------|
| `tests/e2e/bid_review_flow.spec.ts` | 主测试文件 |
| `tests/playwright.config.ts` | Playwright 配置（需要创建） |
| `docs/superpowers/specs/2026-04-14-bid-review-e2e-test-design.md` | 本设计文档 |

## 依赖项

- Playwright (`@playwright/test`)
- 测试文档 (`testdocuments/迪维勒普招标文件.docx`, `testdocuments/迪维勒普投标文件.docx`)
- VNC DISPLAY=:2 环境
- Chrome remote debugging port 9222
