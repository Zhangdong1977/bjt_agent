# RAG Memory

> Hybrid RAG memory system with vector + keyword search for TypeScript/Node.js

[![npm version](https://badge.fury.io/js/rag-memory.svg)](https://www.npmjs.com/package/rag-memory)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A powerful, TypeScript-first memory indexing system that combines **vector embeddings** with **BM25 keyword search** to deliver superior search results for your documents, knowledge bases, and AI applications.

## ✨ Features

- 🔍 **Hybrid Search** - Combines semantic vector search with precise keyword matching
- 🧠 **Multiple Embedding Providers** - OpenAI, Google Gemini, Zhipu AI (智谱AI), or custom providers
- 🇨🇳 **Chinese Optimized** - First-class support for Chinese text with Zhipu AI embeddings
- 📁 **Real-time File Watching** - Automatic index updates when documents change
- 💾 **SQLite Storage** - Fast, reliable indexing with sqlite-vec extension
- 🎯 **TypeScript-First** - Full type safety and excellent IDE support
- ⚡ **Zero-Config Startup** - Works out of the box with sensible defaults
- 🔄 **Embedding Caching** - Avoid re-computing embeddings for unchanged content
- 📊 **Progress Tracking** - Monitor indexing and search operations

## 📦 Installation

```bash
npm install rag-memory
```

### Peer Dependencies

```bash
npm install better-sqlite3
```

### Optional Dependencies

```bash
# For OpenAI embeddings
npm install openai

# For Google Gemini embeddings
npm install @google/generative-ai

# For Zhipu AI embeddings (智谱AI - 推荐 for Chinese)
# No additional dependencies needed!

# For local embeddings (experimental)
npm install node-llama-cpp
```

## 🚀 Quick Start

```typescript
import { createMemoryIndex } from 'rag-memory';

// Create index with OpenAI embeddings
const memory = await createMemoryIndex({
  documentsPath: './docs',
  config: {
    embeddings: {
      provider: 'openai',
      remote: {
        apiKey: process.env.OPENAI_API_KEY!,
      },
    },
  },
});

// Search your documents
const results = await memory.search('How to deploy to production?');

// Results include file path, line numbers, score, and snippet
for (const result of results) {
  console.log(`Found in ${result.path}:${result.startLine}`);
  console.log(`Score: ${(result.score * 100).toFixed(1)}%`);
  console.log(`Snippet: ${result.snippet}\n`);
}

// Always close when done
await memory.close();
```

## 📖 Usage

### Basic Search

```typescript
import { createMemoryIndex } from 'rag-memory';

const memory = await createMemoryIndex({
  documentsPath: './knowledge-base',
});

const results = await memory.search('authentication methods');
```

### Configuration

```typescript
const memory = await createMemoryIndex({
  documentsPath: './docs',
  indexPath: './my-index.sqlite',

  config: {
    // Embedding settings
    embeddings: {
      provider: 'openai',
      model: 'text-embedding-3-small',
      remote: {
        apiKey: process.env.OPENAI_API_KEY!,
      },
    },

    // Chunking strategy
    chunking: {
      tokens: 400,    // Target chunk size
      overlap: 80,    // Overlap between chunks
    },

    // Search behavior
    search: {
      maxResults: 10,
      minScore: 0.35,
      hybrid: {
        enabled: true,
        vectorWeight: 0.7,  // Semantic similarity weight
        textWeight: 0.3,    // Keyword match weight
      },
    },

    // Synchronization
    sync: {
      watch: true,          // Watch for file changes
      watchDebounceMs: 1500,
      onSearch: true,       // Sync before searching
    },
  },
});
```

### Using Different Embedding Providers

#### OpenAI

```typescript
const memory = await createMemoryIndex({
  config: {
    embeddings: {
      provider: 'openai',
      remote: {
        apiKey: process.env.OPENAI_API_KEY!,
        baseUrl: 'https://api.openai.com/v1', // Optional custom endpoint
      },
    },
  },
});
```

#### Google Gemini

```typescript
const memory = await createMemoryIndex({
  config: {
    embeddings: {
      provider: 'gemini',
      remote: {
        apiKey: process.env.GEMINI_API_KEY!,
      },
    },
  },
});
```

#### Zhipu AI (智谱AI) - 推荐 for Chinese

```typescript
const memory = await createMemoryIndex({
  config: {
    embeddings: {
      provider: 'zhipu',
      model: 'embedding-3',
      remote: {
        apiKey: process.env.ZHIPU_API_KEY!,
        dimensions: 1024,  // 可选：512, 768, 1024
      },
    },
  },
});

// 中文搜索示例
const results = await memory.search('如何配置认证');
```

**优势**：
- 🇨🇳 中文优化
- 💰 性价比高
- 🚀 响应快速
- 🎯 1024维高精度向量
- 🔄 支持自定义维度

#### Custom Provider

```typescript
import { createMemoryIndex, type EmbeddingProvider } from 'rag-memory';

const customProvider: EmbeddingProvider = {
  id: 'my-custom',
  model: 'my-model',
  dimensions: 768,

  embedQuery: async (text) => {
    const response = await fetch('https://my-api.com/embed', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    const data = await response.json();
    return data.embedding;
  },

  embedBatch: async (texts) => {
    return Promise.all(texts.map(t => customProvider.embedQuery(t)));
  },
};

const memory = await createMemoryIndex({
  config: {
    embeddings: {
      provider: 'custom',
      customProvider,
    },
  },
});
```

### Reading Files

```typescript
// Read entire file
const file = await memory.readFile('docs/guide.md');
console.log(file.text);

// Read specific lines
const excerpt = await memory.readFile('docs/guide.md', {
  from: 45,
  lines: 20,
});
```

### Manual Sync

```typescript
// Force full reindex
await memory.sync({ force: true });

// Sync with progress tracking
await memory.sync({
  progress: (update) => {
    console.log(`Progress: ${update.completed}/${update.total}`);
    if (update.label) {
      console.log(`Status: ${update.label}`);
    }
  },
});
```

### Status Monitoring

```typescript
const status = memory.status();

console.log('Indexed files:', status.files);
console.log('Indexed chunks:', status.chunks);
console.log('Needs sync:', status.dirty);
console.log('Provider:', status.provider);
console.log('Model:', status.model);
```

## 🎯 Use Cases

### Knowledge Base Search

```typescript
import { createMemoryIndex } from 'rag-memory';

const kb = await createMemoryIndex({
  documentsPath: './company-kb',
});

// Answer customer questions
async function answerQuestion(question: string) {
  const results = await kb.search(question);

  if (results.length === 0) {
    return "I couldn't find relevant information.";
  }

  // Use top results to generate answer
  const context = results[0].snippet;
  return `Based on our knowledge base: ${context}`;
}
```

### Documentation Search

```typescript
const docs = await createMemoryIndex({
  documentsPath: './docs',
  extraPaths: ['./guides', './api-reference'],
});

// Help developers find relevant docs
async function findDocs(query: string) {
  return await docs.search(query, {
    maxResults: 5,
    minScore: 0.5,
  });
}
```

### AI Agent Memory

```typescript
// Give your AI agent long-term memory
const agentMemory = await createMemoryIndex({
  documentsPath: './agent-memory',
  config: {
    chunking: { tokens: 300, overlap: 50 },
    search: {
      hybrid: {
        vectorWeight: 0.8, // Prioritize semantic matching
        textWeight: 0.2,
      },
    },
  },
});

// Agent can recall past decisions
async function recall(query: string) {
  const memories = await agentMemory.search(query);
  return memories.map(m => m.snippet).join('\n');
}
```

### Express API

```typescript
import express from 'express';
import { createMemoryIndex } from 'rag-memory';

const app = express();
const memory = await createMemoryIndex({
  documentsPath: './docs',
});

app.get('/api/search', async (req, res) => {
  const { q } = req.query;
  const results = await memory.search(q as string);
  res.json({ results });
});

app.listen(3000);
```

See [examples/express-api.ts](examples/express-api.ts) for a complete implementation.

## 📚 API Reference

### `createMemoryIndex(options)`

Create a new memory index.

**Parameters:**
- `options.documentsPath` - Path to documents directory (optional)
- `options.indexPath` - Path to SQLite database (optional)
- `options.config` - Partial configuration object (optional)
- `options.initialSync` - Whether to perform initial sync (default: `true`)

**Returns:** `Promise<MemoryIndex>`

### `MemoryIndex`

Main interface for interacting with the memory index.

#### Methods

- `search(query, options?)` - Search indexed documents
- `sync(options?)` - Synchronize index with files
- `readFile(path, options?)` - Read a file from indexed paths
- `status()` - Get index status information
- `close()` - Close the index and release resources

### Configuration Types

See [src/core/types.ts](src/core/types.ts) for complete type definitions.

## 🔧 Advanced Configuration

### Multiple Directories

```typescript
const memory = await createMemoryIndex({
  documentsPath: './primary-docs',
  config: {
    extraPaths: [
      './secondary-docs',
      './archived-docs',
      '/absolute/path/to/docs',
    ],
  },
});
```

### Performance Tuning

```typescript
const memory = await createMemoryIndex({
  config: {
    // Larger chunks for faster indexing
    chunking: { tokens: 800, overlap: 160 },

    // Stricter score threshold
    search: { minScore: 0.6 },

    // Disable file watching for read-only docs
    sync: { watch: false },
  },
});
```

### Caching

```typescript
const memory = await createMemoryIndex({
  config: {
    cache: {
      enabled: true,
      maxEntries: 100000, // Increase for large datasets
    },
  },
});
```

## 🏗️ Architecture

RAG Memory uses a **two-layer storage architecture**:

1. **Document Layer** - Your Markdown files (source of truth)
2. **Index Layer** - SQLite database with vector + FTS indexes

### Hybrid Search Algorithm

```
Query → ┌─────────────┬─────────────┐
         │ Vector      │ Keyword     │
         │ Search      │ (BM25)      │
         └─────────────┴─────────────┘
                   ↓
            Result Fusion
         (weighted combination)
                   ↓
              Final Results
```

- **Vector Search** - Semantic understanding, captures meaning
- **Keyword Search** - Precise matching for IDs, code, terminology
- **Fusion** - Combines both signals for optimal results

## 📖 Examples

- [Basic Usage](examples/basic.ts) - Simple search example
- [Knowledge Base](examples/knowledge-base.ts) - Building a KB system
- [Custom Embedding](examples/custom-embedding.ts) - Using custom embeddings
- [Express API](examples/express-api.ts) - REST API implementation

Run examples with:
```bash
npm run example:basic
npm run example:knowledge
npm run example:custom
```

## 🧪 Testing

We've thoroughly tested RAG Memory with various embedding providers.

### Test Coverage

- ✅ **基础功能** - 索引、同步、状态查询
- ✅ **中文搜索** - 智谱AI embedding测试
- ✅ **英文搜索** - OpenAI/Gemini embedding测试
- ✅ **语义理解** - 同义词召回、上下文理解
- ✅ **边缘情况** - 空查询、特殊字符、错误处理

### Quick Test

```bash
# Build the project
npm run build

# Run basic tests with Mock provider
npx tsx test/phase1-basic.test.ts

# Run comprehensive tests with Zhipu AI
npx tsx test/comprehensive-zhipu.test.ts
```

### Test Results

**Zhipu AI (智谱AI) 完整测试结果**:
- 📊 总测试数: 24
- ✅ 通过: 21 (87.5%)
- ❌ 失败: 3 (12.5%)
- 📚 知识库: 11个文档，71个文本块
- 🚀 平均搜索速度: 150-200ms

**测试覆盖**:
- 基础功能: 75% (3/4)
- 中文搜索: 85.7% (6/7)
- 英文搜索: 100% (4/4)
- 语义理解: 75% (3/4)
- 边缘情况: 100% (5/5)

详细测试报告请查看: [TEST_REPORT.md](TEST_REPORT.md)

### 智谱AI 测试示例

```typescript
// 使用智谱AI进行中文搜索
const memory = await createMemoryIndex({
  documentsPath: './docs',
  config: {
    embeddings: {
      provider: 'zhipu',
      model: 'embedding-3',
      remote: {
        apiKey: process.env.ZHIPU_API_KEY!,
        dimensions: 1024,
      },
    },
    extraPaths: ['./docs'],
  },
});

// 中文语义搜索
const results = await memory.search('如何配置OAuth认证');
// → docs/authentication.md (score: 0.465) ✅

const results2 = await memory.search('部署到生产环境');
// → docs/deployment.md (score: 0.390) ✅

const results3 = await memory.search('数据库优化');
// → docs/database-config.md (score: 0.378) ✅
```

## 🤝 Contributing

Contributions are welcome! This package is extracted from [OpenClaw](https://github.com/openclaw/openclaw).

## 📄 License

MIT © OpenClaw contributors

## 🙏 Acknowledgments

Built with inspiration from:
- [OpenClaw](https://github.com/openclaw/openclaw) - Original memory system
- [better-sqlite3](https://github.com/WiseLibs/better-sqlite3) - SQLite driver
- [sqlite-vec](https://github.com/asg017/sqlite-vec) - Vector search extension

---

**Made with ❤️ by the OpenClaw community**
