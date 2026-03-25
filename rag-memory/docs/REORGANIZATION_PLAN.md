# Docs 目录重组方案

## 当前状态

- **文档总数**: 11个
- **总行数**: 4933行
- **目录**: 所有文档平铺在 `docs/` 目录下
- **问题**: 随着文档增多，难以管理和查找

---

## 方案A: 按用户角色分类 (推荐)

### 目录结构

```
docs/
├── README.md                        # 文档导航
├── getting-started/                # 入门指南
│   ├── authentication.md           # 认证和授权
│   ├── api-usage.md                # API使用指南
│   └── zhipu-ai-example.md         # 智谱AI使用
│
├── development/                     # 开发指南
│   ├── frontend-guide.md           # 前端开发
│   ├── backend-guide.md            # 后端开发
│   ├── database-config.md          # 数据库配置
│   └── testing.md                  # 测试指南
│
└── operations/                      # 运维指南
    ├── deployment.md               # 部署指南
    ├── security.md                 # 安全性
    ├── performance-optimization.md # 性能优化
    └── error-handling.md           # 错误处理
```

### 优点

- ✅ 符合用户工作流程（入门 → 开发 → 运维）
- ✅ 清晰的角色划分
- ✅ 易于扩展

### 适用场景

- 面向开发团队的知识库
- 从入门到上线的完整流程

---

## 方案B: 按文档类型分类

### 目录结构

```
docs/
├── README.md                        # 文档导航
├── guides/                          # 使用指南
│   ├── authentication/
│   │   └── index.md
│   ├── api-usage/
│   │   └── index.md
│   └── zhipu-ai/
│       └── index.md
│
├── tutorials/                       # 教程
│   ├── frontend-development.md
│   ├── backend-development.md
│   └── database-setup.md
│
└── reference/                       # 参考文档
    ├── deployment.md
    ├── security.md
    ├── performance.md
    ├── error-handling.md
    └── testing.md
```

### 优点

- ✅ 教程和参考文档分离
- ✅ 适合大型知识库
- ✅ 便于不同查找方式

### 适用场景

- 大型项目文档
- 需要教程和参考并存的场景

---

## 方案C: 按技术栈分类

### 目录结构

```
docs/
├── README.md
├── core/                            # 核心概念
│   ├── authentication.md
│   └── api-usage.md
│
├── frontend/                        # 前端相关
│   ├── guide.md
│   └── testing.md
│
├── backend/                         # 后端相关
│   ├── guide.md
│   └── database.md
│
├── devops/                          # 运维相关
│   ├── deployment.md
│   ├── security.md
│   ├── performance.md
│   └── error-handling.md
│
└── integrations/                    # 第三方集成
    └── zhipu-ai.md
```

### 优点

- ✅ 按技术领域划分
- ✅ 专业人员快速定位
- ✅ 符合技术栈习惯

### 适用场景

- 技术文档库
- 面向技术团队的参考文档

---

## 推荐方案对比

| 维度 | 方案A (角色) | 方案B (类型) | 方案C (技术) |
|------|-------------|-------------|-------------|
| 易用性 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| 扩展性 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 维护性 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ |
| 查找效率 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ |

---

## 方案A 详细实施计划

### 第一步: 创建目录结构

```bash
cd docs
mkdir -p getting-started
mkdir -p development
mkdir -p operations
```

### 第二步: 移动文件

```bash
# 入门指南
mv authentication.md getting-started/
mv api-usage.md getting-started/
mv zhipu-ai-example.md getting-started/

# 开发指南
mv frontend-guide.md development/
mv backend-guide.md development/
mv database-config.md development/
mv testing.md development/

# 运维指南
mv deployment.md operations/
mv security.md operations/
mv performance-optimization.md operations/
mv error-handling.md operations/
```

### 第三步: 创建导航文档

创建 `docs/README.md`:

```markdown
# RAG Memory 文档中心

## 📚 快速导航

### 🚀 入门指南 (Getting Started)

- [认证和授权](getting-started/authentication.md) - 系统认证方式
- [API使用指南](getting-started/api-usage.md) - REST API 使用
- [智谱AI集成](getting-started/zhipu-ai-example.md) - 中文优化配置

### 💻 开发指南 (Development)

- [前端开发](development/frontend-guide.md) - React + TypeScript
- [后端开发](development/backend-guide.md) - Node.js + Express
- [数据库配置](development/database-config.md) - PostgreSQL/MySQL/SQLite
- [测试指南](development/testing.md) - 单元测试和集成测试

### 🔧 运维指南 (Operations)

- [部署指南](operations/deployment.md) - Docker/K8s/云平台
- [安全性](operations/security.md) - 认证、授权、加密
- [性能优化](operations/performance-optimization.md) - 缓存、索引、调优
- [错误处理](operations/error-handling.md) - 日志、监控、告警

## 🎯 按场景查找

### 我想...

- **开始使用** → [入门指南](getting-started/)
- **开发功能** → [开发指南](development/)
- **部署上线** → [运维指南](operations/)
- **解决问题** → [错误处理](operations/error-handling.md)

### 搜索文档

使用全局搜索 (Ctrl+K / Cmd+K) 快速查找内容。
```

### 第四步: 更新配置

如果需要更新 rag-memory 的 `extraPaths` 配置:

```typescript
const memory = await createMemoryIndex({
  documentsPath: './docs',
  config: {
    extraPaths: [
      './docs/getting-started',
      './docs/development',
      './docs/operations',
    ],
  },
});
```

---

## 迁移检查清单

- [ ] 备份现有文档
- [ ] 创建新的目录结构
- [ ] 移动文件到新目录
- [ ] 创建/更新 README.md 导航
- [ ] 更新内部链接（如果有）
- [ ] 更新 rag-memory 配置
- [ ] 运行测试验证
- [ ] 提交 Git 更改

---

## 需要我帮你执行重组吗？

请选择：
1. **方案A** - 按用户角色分类（推荐）
2. **方案B** - 按文档类型分类
3. **方案C** - 按技术栈分类
4. **自定义** - 告诉我你的想法

我会立即帮你完成重组！
