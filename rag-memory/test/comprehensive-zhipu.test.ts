/**
 * RAG Memory 完整功能测试套件 - 使用智谱AI Embedding
 *
 * 测试范围：
 * 1. 基础功能（索引、同步、状态）
 * 2. 中文知识召回
 * 3. 英文搜索
 * 4. 混合语言搜索
 * 5. 语义理解和同义词
 * 6. 边缘情况处理
 */

import { createMemoryIndex } from '../src/index.js';

const ZHIPU_API_KEY = '7d3c1932802847fb8c699744ec086c95.gc9p0sCWGJFyWInr';

interface TestResult {
  name: string;
  category: string;
  passed: boolean;
  duration: number;
  error?: string;
  details?: any;
}

const results: TestResult[] = [];

async function runTest(
  category: string,
  name: string,
  testFn: () => Promise<void>
): Promise<void> {
  const start = Date.now();
  try {
    await testFn();
    const duration = Date.now() - start;
    results.push({ category, name, passed: true, duration });
    console.log(`✅ [${category}] ${name} (${duration}ms)`);
  } catch (error) {
    const duration = Date.now() - start;
    const errorMsg = error instanceof Error ? error.message : String(error);
    results.push({ category, name, passed: false, duration, error: errorMsg });
    console.log(`❌ [${category}] ${name} (${duration}ms) - ${errorMsg}`);
  }
}

// ============== 测试套件 ==============

async function runComprehensiveTests() {
  console.log('\n🧪 RAG Memory 完整功能测试套件');
  console.log('🤖 使用智谱AI Embedding (embedding-3, 1024维)\n');
  console.log('='.repeat(70));

  // ============== Phase 1: 基础功能测试 ==============

  console.log('\n📦 Phase 1: 基础功能测试\n');

  await runTest('基础功能', '1.1 创建索引', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    if (!memory) {
      throw new Error('Failed to create index');
    }

    await memory.close();
  });

  await runTest('基础功能', '1.2 状态查询', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const status = memory.status();

    if (typeof status.files !== 'number') {
      throw new Error('status.files is not a number');
    }
    if (typeof status.chunks !== 'number') {
      throw new Error('status.chunks is not a number');
    }
    if (!status.provider || status.provider !== 'zhipu') {
      throw new Error('Wrong provider');
    }
    if (!status.model || status.model !== 'embedding-3') {
      throw new Error('Wrong model');
    }

    await memory.close();
  });

  await runTest('基础功能', '1.3 文件同步', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    await memory.sync();

    const status = memory.status();
    if (status.files === 0) {
      throw new Error('No files indexed');
    }

    await memory.close();
  });

  await runTest('基础功能', '1.4 读取文件', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const file = await memory.readFile('docs/api-usage.md', {
      from: 1,
      lines: 5,
    });

    if (!file.text) {
      throw new Error('File text is empty');
    }
    if (file.path !== 'docs/api-usage.md') {
      throw new Error('Wrong file path');
    }

    await memory.close();
  });

  // ============== Phase 2: 中文知识召回测试 ==============

  console.log('\n🇨🇳 Phase 2: 中文知识召回测试\n');

  await runTest('中文搜索', '2.1 查询"如何配置认证"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    await memory.sync();

    const results = await memory.search('如何配置认证', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    const topResult = results[0];
    if (!topResult.path.includes('authentication')) {
      throw new Error(`Expected authentication doc, got ${topResult.path}`);
    }

    await memory.close();
  });

  await runTest('中文搜索', '2.2 查询"部署"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('部署', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    const topResult = results[0];
    if (!topResult.path.includes('deployment')) {
      throw new Error(`Expected deployment doc, got ${topResult.path}`);
    }

    await memory.close();
  });

  await runTest('中文搜索', '2.3 查询"数据库配置"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('数据库配置', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    const topResult = results[0];
    if (!topResult.path.includes('database')) {
      throw new Error(`Expected database doc, got ${topResult.path}`);
    }

    await memory.close();
  });

  await runTest('中文搜索', '2.4 查询"前端开发"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('前端开发', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    const topResult = results[0];
    if (!topResult.path.includes('frontend')) {
      throw new Error(`Expected frontend doc, got ${topResult.path}`);
    }

    await memory.close();
  });

  await runTest('中文搜索', '2.5 查询"安全性"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('安全性', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    const topResult = results[0];
    if (!topResult.path.includes('security')) {
      throw new Error(`Expected security doc, got ${topResult.path}`);
    }

    await memory.close();
  });

  await runTest('中文搜索', '2.6 查询"性能优化"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('性能优化', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    const topResult = results[0];
    if (!topResult.path.includes('performance')) {
      throw new Error(`Expected performance doc, got ${topResult.path}`);
    }

    await memory.close();
  });

  await runTest('中文搜索', '2.7 查询"错误处理"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('错误处理', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    const topResult = results[0];
    if (!topResult.path.includes('error')) {
      throw new Error(`Expected error-handling doc, got ${topResult.path}`);
    }

    await memory.close();
  });

  // ============== Phase 3: 英文搜索测试 ==============

  console.log('\n🇺🇸 Phase 3: 英文搜索测试\n');

  await runTest('英文搜索', '3.1 查询"API usage"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('API usage', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    const topResult = results[0];
    if (!topResult.path.includes('api')) {
      throw new Error(`Expected API doc, got ${topResult.path}`);
    }

    await memory.close();
  });

  await runTest('英文搜索', '3.2 查询"deployment guide"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('deployment guide', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    const topResult = results[0];
    if (!topResult.path.includes('deployment')) {
      throw new Error(`Expected deployment doc, got ${topResult.path}`);
    }

    await memory.close();
  });

  await runTest('英文搜索', '3.3 查询"security best practices"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('security best practices', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    const topResult = results[0];
    if (!topResult.path.includes('security')) {
      throw new Error(`Expected security doc, got ${topResult.path}`);
    }

    await memory.close();
  });

  await runTest('英文搜索', '3.4 查询"database optimization"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('database optimization', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    const foundDatabase = results.some(r => r.path.includes('database'));
    if (!foundDatabase) {
      throw new Error('Expected to find database-related doc');
    }

    await memory.close();
  });

  // ============== Phase 4: 语义理解和同义词测试 ==============

  console.log('\n🧠 Phase 4: 语义理解和同义词测试\n');

  await runTest('语义理解', '4.1 同义词"登录"→"认证"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('登录', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    // 应该找到 authentication.md
    const found = results.some(r => r.path.includes('authentication'));
    if (!found) {
      throw new Error('Expected to find authentication doc for "登录"');
    }

    await memory.close();
  });

  await runTest('语义理解', '4.2 同义词"密码"→"安全"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('密码', {
      maxResults: 5,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    // 应该找到 security.md 或 authentication.md
    const found = results.some(r =>
      r.path.includes('security') || r.path.includes('authentication')
    );
    if (!found) {
      throw new Error('Expected to find security-related doc for "密码"');
    }

    await memory.close();
  });

  await runTest('语义理解', '4.3 上下文"速度慢"→"性能优化"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('速度慢怎么办', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    // 应该找到 performance-optimization.md
    const found = results.some(r => r.path.includes('performance'));
    if (!found) {
      throw new Error('Expected to find performance doc for "速度慢"');
    }

    await memory.close();
  });

  await runTest('语义理解', '4.4 相关概念"报错"→"错误处理"', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('报错了', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results) || results.length === 0) {
      throw new Error('No results returned');
    }

    // 应该找到 error-handling.md
    const found = results.some(r => r.path.includes('error'));
    if (!found) {
      throw new Error('Expected to find error-handling doc for "报错"');
    }

    await memory.close();
  });

  // ============== Phase 5: 边缘情况测试 ==============

  console.log('\n🔍 Phase 5: 边缘情况测试\n');

  await runTest('边缘情况', '5.1 空查询', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('', {
      maxResults: 5,
      minScore: 0.1,
    });

    // 空查询应该返回空数组或所有结果
    if (!Array.isArray(results)) {
      throw new Error('Empty query should return array');
    }

    await memory.close();
  });

  await runTest('边缘情况', '5.2 特殊字符查询', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const results = await memory.search('API @#$%^&*()', {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results)) {
      throw new Error('Special chars query should return array');
    }

    await memory.close();
  });

  await runTest('边缘情况', '5.3 超长查询', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    const longQuery = 'API '.repeat(100);
    const results = await memory.search(longQuery, {
      maxResults: 3,
      minScore: 0.1,
    });

    if (!Array.isArray(results)) {
      throw new Error('Long query should return array');
    }

    await memory.close();
  });

  await runTest('边缘情况', '5.4 无效文件路径', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    try {
      await memory.readFile('nonexistent/file.md', {
        from: 1,
        lines: 10,
      });
      throw new Error('Should throw error for nonexistent file');
    } catch (error) {
      if ((error as Error).message === 'Should throw error for nonexistent file') {
        throw error;
      }
      // Expected error
    }

    await memory.close();
  });

  await runTest('边缘情况', '5.5 资源清理', async () => {
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-comprehensive.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024,
          },
        },
        storage: {
          vectorEnabled: true,
          ftsEnabled: true,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    await memory.close();

    // Try to use after close - should fail or handle gracefully
    try {
      await memory.search('test');
      // If it doesn't throw, that's okay too
    } catch (error) {
      // Expected - search after close should fail
    }
  });

  // ============== 生成测试报告 ==============

  console.log('\n' + '='.repeat(70));
  console.log('📊 测试报告\n');

  // 按类别分组统计
  const byCategory: Record<string, { passed: number; failed: number; durations: number[] }> = {};

  for (const result of results) {
    if (!byCategory[result.category]) {
      byCategory[result.category] = { passed: 0, failed: 0, durations: [] };
    }
    if (result.passed) {
      byCategory[result.category].passed++;
    } else {
      byCategory[result.category].failed++;
    }
    byCategory[result.category].durations.push(result.duration);
  }

  // 打印分类统计
  for (const [category, stats] of Object.entries(byCategory)) {
    const total = stats.passed + stats.failed;
    const avgDuration = stats.durations.reduce((a, b) => a + b, 0) / stats.durations.length;
    console.log(`${category}:`);
    console.log(`  总计: ${total} | ✅ 通过: ${stats.passed} | ❌ 失败: ${stats.failed} | ⏱️ 平均耗时: ${avgDuration.toFixed(0)}ms`);
  }

  console.log('\n' + '-'.repeat(70));

  // 总体统计
  const totalTests = results.length;
  const totalPassed = results.filter(r => r.passed).length;
  const totalFailed = results.filter(r => !r.passed).length;
  const totalDuration = results.reduce((sum, r) => sum + r.duration, 0);
  const avgDuration = totalDuration / totalTests;

  console.log('\n📈 总体统计:');
  console.log(`  总测试数: ${totalTests}`);
  console.log(`  通过: ${totalPassed} ✅ (${((totalPassed / totalTests) * 100).toFixed(1)}%)`);
  console.log(`  失败: ${totalFailed} ${totalFailed > 0 ? '❌' : '✅'} (${((totalFailed / totalTests) * 100).toFixed(1)}%)`);
  console.log(`  总耗时: ${totalDuration}ms`);
  console.log(`  平均耗时: ${avgDuration.toFixed(0)}ms`);

  // 打印失败的测试
  if (totalFailed > 0) {
    console.log('\n❌ 失败的测试:');
    results.filter(r => !r.passed).forEach(r => {
      console.log(`  [${r.category}] ${r.name}: ${r.error}`);
    });
  }

  console.log('\n' + '='.repeat(70));

  // 获取索引状态
  const finalMemory = await createMemoryIndex({
    documentsPath: '.',
    indexPath: './test-comprehensive.sqlite',
    config: {
      embeddings: {
        provider: 'zhipu',
        model: 'embedding-3',
        remote: {
          apiKey: ZHIPU_API_KEY,
          dimensions: 1024,
        },
      },
      storage: {
        vectorEnabled: true,
        ftsEnabled: true,
      },
      sync: {
        watch: false,
      },
      extraPaths: ['./docs'],
    },
    initialSync: false,
  });

  const finalStatus = finalMemory.status();
  console.log('\n📚 知识库状态:');
  console.log(`  Provider: ${finalStatus.provider}`);
  console.log(`  Model: ${finalStatus.model}`);
  console.log(`  文件数: ${finalStatus.files}`);
  console.log(`  文本块数: ${finalStatus.chunks}`);

  await finalMemory.close();

  console.log('\n' + '='.repeat(70));
  console.log(`\n${totalFailed === 0 ? '🎉 所有测试通过！' : '⚠️ 部分测试失败'}\n`);

  return {
    passed: totalPassed,
    failed: totalFailed,
    total: totalTests,
    results,
  };
}

// 运行测试
runComprehensiveTests()
  .then((summary) => {
    process.exit(summary.failed > 0 ? 1 : 0);
  })
  .catch((error) => {
    console.error('\n❌ 测试运行失败:', error);
    process.exit(1);
  });
