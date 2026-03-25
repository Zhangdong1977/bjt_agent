# 智谱AI Embedding 使用指南

## ✅ 测试结果

```
📊 Summary:
   - Provider: zhipu
   - Model: embedding-3
   - Files indexed: 2
   - Chunks created: 6
   - Embeddings generated: 6
   - Embedding dimensions: 1024
```

### 搜索测试结果

#### 中文查询测试
- ✅ **"如何配置认证"** → 找到 `authentication.md` (score: 0.350)
- ✅ **"部署"** → 找到 `deployment.md` (score: 0.390)
- ✅ **"API密钥"** → 找到 `authentication.md` (score: 0.387)
- ✅ **"OAuth"** → 找到 `authentication.md` (score: 0.465)

---

## 🚀 快速开始

### 1. 安装和构建

```bash
cd rag-memory
npm install
npm run build
```

### 2. 使用智谱AI Embeddings

```typescript
import { createMemoryIndex } from 'rag-memory';

const memory = await createMemoryIndex({
  documentsPath: './docs',
  config: {
    embeddings: {
      provider: 'zhipu',
      model: 'embedding-3',
      remote: {
        apiKey: 'your-zhipu-api-key',
        dimensions: 1024, // 可选：自定义维度
      },
    },
    extraPaths: ['./docs'],
  },
});

// 中文搜索
const results = await memory.search('如何配置OAuth认证');
console.log(results);

await memory.close();
```

---

## 📝 配置选项

### 基础配置

```typescript
{
  embeddings: {
    provider: 'zhipu',
    model: 'embedding-3',
    remote: {
      apiKey: 'your-api-key',
    },
  }
}
```

### 完整配置

```typescript
{
  embeddings: {
    provider: 'zhipu',
    model: 'embedding-3',
    remote: {
      apiKey: 'your-api-key',
      dimensions: 1024,      // 可选：自定义维度
      baseUrl: undefined,     // 可选：自定义API地址
      headers: {},            // 可选：额外HTTP头
    },
  },
  storage: {
    vectorEnabled: false,     // sqlite-vec（可选）
    ftsEnabled: false,        // 全文搜索（可选）
  },
  chunking: {
    tokens: 400,              // 分块大小
    overlap: 80,              // 重叠大小
  },
  search: {
    maxResults: 10,           // 最大结果数
    minScore: 0.35,           // 最小分数
    hybrid: {
      enabled: true,          // 混合搜索
      vectorWeight: 0.7,      // 向量权重
      textWeight: 0.3,        // 关键词权重
    },
  },
}
```

---

## 🔑 获取智谱AI API Key

1. 访问 [智谱AI开放平台](https://open.bigmodel.cn/)
2. 注册/登录账号
3. 进入 API Keys 页面
4. 创建新的 API Key
5. 复制 API Key 格式：`id.secret`

---

## 📊 性能和定价

### embedding-3 模型

- **维度**: 默认 1024（可自定义 256-1024）
- **费用**: 参考智谱AI官方定价
- **速度**: 快速响应
- **语言**: 优化中文和多语言

### 优化建议

1. **降低维度**（如果不需要高精度）：
   ```typescript
   dimensions: 512  // 比默认1024更快更便宜
   ```

2. **启用缓存**：
   ```typescript
   cache: {
     enabled: true,
     maxEntries: 50000,
   }
   ```

3. **批量索引**：
   ```typescript
   // 一次性索引多个文件
   extraPaths: ['./docs', './kb', './wiki']
   ```

---

## 💡 使用示例

### 知识库搜索

```typescript
import { createMemoryIndex } from 'rag-memory';

const kb = await createMemoryIndex({
  documentsPath: './knowledge-base',
  config: {
    embeddings: {
      provider: 'zhipu',
      remote: {
        apiKey: process.env.ZHIPU_API_KEY!,
        dimensions: 512,
      },
    },
    extraPaths: ['./docs', './faq'],
  },
});

// 搜索问题
const answer = async (question: string) => {
  const results = await kb.search(question);
  return results.map(r => r.snippet).join('\n\n');
};

const response = await answer('如何重置密码？');
console.log(response);
```

### 中英文混合搜索

```typescript
// 中文查询
const results1 = await memory.search('认证方法');

// 英文查询
const results2 = await memory.search('authentication methods');

// 混合查询
const results3 = await memory.search('OAuth 认证配置');
```

### 语义理解

```typescript
// 相似意思的查询
const queries = [
  '如何登录',
  '登录方式',
  '账号认证',
  '登录系统',
];

for (const query of queries) {
  const results = await memory.search(query);
  console.log(`"${query}" → ${results[0]?.path}`);
}
```

---

## 🔧 高级用法

### 自定义维度

```typescript
// 512维 - 更快更便宜，适合简单场景
{
  remote: { dimensions: 512 }
}

// 768维 - 平衡性能和精度
{
  remote: { dimensions: 768 }
}

// 1024维 - 最高精度（默认）
{
  remote: { dimensions: 1024 }
}
```

### 动态切换Provider

```typescript
// 开发环境用Mock，生产环境用智谱AI
const provider = process.env.NODE_ENV === 'production' ? 'zhipu' : 'custom';

const memory = await createMemoryIndex({
  config: {
    embeddings: {
      provider,
      // ... 配置
    },
  },
});
```

### 组合多个知识库

```typescript
const companyKB = await createMemoryIndex({
  documentsPath: './company-kb',
  config: {
    embeddings: {
      provider: 'zhipu',
      remote: { apiKey: process.env.ZHIPU_API_KEY! },
    },
    extraPaths: [
      './hr/policies',
      './engineering/docs',
      './sales/materials',
    ],
  },
});
```

---

## 🐛 故障排除

### API Key 错误

```
Error: Zhipu AI API key is required
```

**解决**: 检查 API Key 格式是否正确：`id.secret`

### 网络错误

```
Error: Zhipu AI API error: fetch failed
```

**解决**:
1. 检查网络连接
2. 确认智谱AI API 服务状态
3. 检查防火墙设置

### 401 Unauthorized

```
Error: Zhipu AI API error: Unauthorized
```

**解决**:
1. 验证 API Key 是否有效
2. 检查 API Key 是否有足够余额
3. 确认 API Key 权限

---

## 📚 相关资源

- [智谱AI开放平台](https://open.bigmodel.cn/)
- [Embedding API 文档](https://open.bigmodel.cn/dev/api#embedding)
- [定价页面](https://open.bigmodel.cn/pricing)

---

## ✅ 测试验证

运行测试：

```bash
cd rag-memory
npx tsx test/zhipu.test.ts
```

预期输出：
- ✅ 成功连接智谱AI API
- ✅ 生成 embeddings
- ✅ 索引文档
- ✅ 中文搜索工作正常
- ✅ 语义理解正确

---

**状态**: 生产就绪 ✅

**最后更新**: 2025-02-01
