/**
 * Phase 1: 基础功能测试（使用 Mock Provider）
 */

import { createMemoryIndex, type EmbeddingProvider } from '../src/index.js';

// Mock Provider for testing without API calls
class MockEmbeddingProvider implements EmbeddingProvider {
  id = 'mock';
  model = 'mock-test-model';
  dimensions = 4;

  async embedQuery(text: string): Promise<number[]> {
    // Deterministic mock embeddings based on text hash
    const hash = this.hashText(text);
    return [
      Math.sin(hash) * 0.5,
      Math.cos(hash) * 0.5,
      Math.sin(hash * 2) * 0.5,
      Math.cos(hash * 2) * 0.5,
    ];
  }

  async embedBatch(texts: string[]): Promise<number[][]> {
    return Promise.all(texts.map((t) => this.embedQuery(t)));
  }

  private hashText(text: string): number {
    let hash = 0;
    for (let i = 0; i < text.length; i++) {
      hash = ((hash << 5) - hash) + text.charCodeAt(i);
      hash |= 0;
    }
    return hash;
  }
}

interface TestResult {
  name: string;
  passed: boolean;
  duration: number;
  error?: string;
  details?: any;
}

const results: TestResult[] = [];

async function runTest(name: string, testFn: () => Promise<void>): Promise<void> {
  const start = Date.now();
  try {
    await testFn();
    const duration = Date.now() - start;
    results.push({ name, passed: true, duration });
    console.log(`✅ ${name} (${duration}ms)`);
  } catch (error) {
    const duration = Date.now() - start;
    const errorMsg = error instanceof Error ? error.message : String(error);
    results.push({ name, passed: false, duration, error: errorMsg });
    console.log(`❌ ${name} (${duration}ms) - ${errorMsg}`);
  }
}

async function phase1() {
  console.log('🔬 Phase 1: 基础功能测试\n');
  console.log('='.repeat(60));

  // Test 1: 包构建
  await runTest('1.1 包构建验证', async () => {
    const fs = await import('fs');
    const path = await import('path');

    // 检查关键文件存在
    const distPath = 'dist';
    const requiredFiles = [
      'index.js',
      'index.d.ts',
      'core/manager.js',
      'embeddings/zhipu.js',
      'embeddings/provider.js',
      'search/hybrid.js',
    ];

    for (const file of requiredFiles) {
      const filePath = path.join(distPath, file);
      if (!fs.existsSync(filePath)) {
        throw new Error(`Missing file: ${filePath}`);
      }
    }
  });

  // Test 2: 配置系统
  await runTest('1.2 配置系统', async () => {
    const { resolveConfig, validateConfig } = await import('../dist/config/index.js');

    const config = resolveConfig({
      storage: {
        path: './test.sqlite',
        workspaceDir: './docs',
      },
    });

    if (!config.storage.path) {
      throw new Error('Config path is empty');
    }
    if (!config.storage.workspaceDir) {
      throw new Error('Config workspaceDir is empty');
    }

    validateConfig(config);
  });

  // Test 3: 创建索引
  await runTest('1.3 创建索引', async () => {
    const { createMemoryIndex } = await import('../dist/index.js');

    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-1-3.sqlite',
      config: {
        embeddings: {
          provider: 'custom',
          customProvider: new MockEmbeddingProvider(),
        },
        storage: {
          vectorEnabled: false,
          ftsEnabled: false,
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

  // Test 4: 文件索引
  await runTest('1.4 文件索引', async () => {
    const { createMemoryIndex } = await import('../dist/index.js');

    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-1-4.sqlite',
      config: {
        embeddings: {
          provider: 'custom',
          customProvider: new MockEmbeddingProvider(),
        },
        storage: {
          vectorEnabled: false,
          ftsEnabled: false,
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

  // Test 5: 搜索功能
  await runTest('1.5 搜索功能', async () => {
    const { createMemoryIndex } = await import('../dist/index.js');

    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-1-5.sqlite',
      config: {
        embeddings: {
          provider: 'custom',
          customProvider: new MockEmbeddingProvider(),
        },
        storage: {
          vectorEnabled: false,
          ftsEnabled: false,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
    });

    await memory.sync();

    const results = await memory.search('authentication', {
      maxResults: 5,
      minScore: 0.1,
    });

    if (!Array.isArray(results)) {
      throw new Error('Search did not return array');
    }

    await memory.close();
  });

  // Test 6: 读取文件
  await runTest('1.6 读取文件', async () => {
    const { createMemoryIndex } = await import('../dist/index.js');

    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-1-6.sqlite',
      config: {
        embeddings: {
          provider: 'custom',
          customProvider: new MockEmbeddingProvider(),
        },
        storage: {
          vectorEnabled: false,
          ftsEnabled: false,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
    });

    const file = await memory.readFile('docs/authentication.md', {
      from: 1,
      lines: 10,
    });

    if (!file.text) {
      throw new Error('File text is empty');
    }
    if (file.path !== 'docs/authentication.md') {
      throw new Error('Wrong file path');
    }

    await memory.close();
  });

  // Test 7: 状态查询
  await runTest('1.7 状态查询', async () => {
    const { createMemoryIndex } = await import('../dist/index.js');

    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-1-7.sqlite',
      config: {
        embeddings: {
          provider: 'custom',
          customProvider: new MockEmbeddingProvider(),
        },
        storage: {
          vectorEnabled: false,
          ftsEnabled: false,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
    });

    const status = memory.status();

    if (typeof status.files !== 'number') {
      throw new Error('status.files is not a number');
    }
    if (typeof status.chunks !== 'number') {
      throw new Error('status.chunks is not a number');
    }
    if (typeof status.dirty !== 'boolean') {
      throw new Error('status.dirty is not a boolean');
    }
    if (!status.provider) {
      throw new Error('status.provider is empty');
    }

    await memory.close();
  });

  // Test 8: 资源清理
  await runTest('1.8 资源清理', async () => {
    const { createMemoryIndex } = await import('../dist/index.js');

    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-1-8.sqlite',
      config: {
        embeddings: {
          provider: 'custom',
          customProvider: new MockEmbeddingProvider(),
        },
        storage: {
          vectorEnabled: false,
          ftsEnabled: false,
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
    });

    await memory.close();

    // Try to use after close should fail
    try {
      await memory.search('test');
      throw new Error('Should not allow search after close');
    } catch (error) {
      if ((error as Error).message === 'Should not allow search after close') {
        // search() didn't throw, this is unexpected
        throw error;
      }
      // Expected: search() should throw "Index is closed"
      if ((error as Error).message !== 'Index is closed') {
        throw error;
      }
    }
  });

  // Print summary
  console.log('\n' + '='.repeat(60));
  console.log('Phase 1 测试总结\n');

  const passed = results.filter(r => r.passed).length;
  const failed = results.filter(r => !r.passed).length;
  const totalDuration = results.reduce((sum, r) => sum + r.duration, 0);

  console.log(`总测试数: ${results.length}`);
  console.log(`通过: ${passed} ✅`);
  console.log(`失败: ${failed} ${failed > 0 ? '❌' : '✅'}`);
  console.log(`总耗时: ${totalDuration}ms`);
  console.log(`平均耗时: ${(totalDuration / results.length).toFixed(0)}ms`);

  if (failed > 0) {
    console.log('\n失败的测试:');
    results.filter(r => !r.passed).forEach(r => {
      console.log(`  ❌ ${r.name}: ${r.error}`);
    });
  }

  return results;
}

// Run Phase 1
phase1().then((results) => {
  const allPassed = results.every(r => r.passed);
  process.exit(allPassed ? 0 : 1);
}).catch((error) => {
  console.error('Phase 1 测试失败:', error);
  process.exit(1);
});
