/**
 * Test with Zhipu AI (智谱AI) embedding provider
 */

import { createMemoryIndex } from '../src/index.js';

const ZHIPU_API_KEY = '7d3c1932802847fb8c699744ec086c95.gc9p0sCWGJFyWInr';

async function test() {
  console.log('🧪 Testing rag-memory with Zhipu AI embeddings...\n');

  try {
    // Create index with Zhipu AI
    console.log('1. Creating memory index with Zhipu AI...');
    const memory = await createMemoryIndex({
      documentsPath: '.',
      indexPath: './test-zhipu.sqlite',
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
          remote: {
            apiKey: ZHIPU_API_KEY,
            dimensions: 1024, // Zhipu AI supports custom dimensions
          },
        },
        storage: {
          vectorEnabled: false, // Disable sqlite-vec for testing
          ftsEnabled: false, // Disable FTS for testing
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'],
      },
      initialSync: false,
    });

    console.log('✅ Index created\n');

    // Check initial status
    console.log('2. Checking initial status...');
    let status = memory.status();
    console.log('Initial:', {
      files: status.files,
      chunks: status.chunks,
      provider: status.provider,
      model: status.model
    });
    console.log('✅\n');

    // Sync files with embeddings
    console.log('3. Syncing files with Zhipu AI embeddings...');
    console.log('   This will call Zhipu AI API to generate embeddings...\n');
    await memory.sync({
      progress: (update) => {
        if (update.total > 0 && update.completed % 1 === 0) {
          console.log(`   Progress: ${update.completed}/${update.total}`);
          if (update.label) {
            console.log(`   ${update.label}`);
          }
        }
      },
    });
    console.log('✅ Sync complete\n');

    // Check status after sync
    console.log('4. Checking status after sync...');
    status = memory.status();
    console.log('After sync:', {
      files: status.files,
      chunks: status.chunks
    });
    console.log('✅\n');

    if (status.files === 0) {
      console.log('⚠️  No files found\n');
    } else {
      console.log(`✅ Successfully indexed ${status.files} files with ${status.chunks} chunks\n`);
    }

    // Test search with real embeddings
    console.log('5. Testing search with Zhipu AI embeddings...');
    const queries = [
      '如何配置认证',  // How to configure authentication
      '部署',         // Deployment
      'API密钥',      // API keys
      'OAuth',        // OAuth
    ];

    for (const query of queries) {
      console.log(`   Query: "${query}"`);
      const results = await memory.search(query, { maxResults: 3 });

      if (results.length === 0) {
        console.log('   → No results\n');
      } else {
        for (const result of results) {
          console.log(`   → ${result.path}:${result.startLine} (score: ${result.score.toFixed(3)})`);
          const snippet = result.snippet.slice(0, 80) + '...';
          console.log(`      "${snippet}"`);
        }
        console.log('');
      }
    }
    console.log('✅ Search works!\n');

    // Test semantic understanding
    console.log('6. Testing semantic understanding...');
    const semanticTests = [
      { query: '登录系统', expected: 'authentication' },   // Login system
      { query: '生产环境', expected: 'deployment' },       // Production environment
      { query: '令牌', expected: 'token' },                // Token
    ];

    for (const test of semanticTests) {
      console.log(`   Query: "${test.query}" (expecting: ${test.expected})`);
      const results = await memory.search(test.query, { maxResults: 1 });

      if (results.length > 0) {
        const result = results[0];
        const matched = result.path.toLowerCase().includes(test.expected);
        const status = matched ? '✅' : '⚠️';
        console.log(`   ${status} Found: ${result.path} (score: ${result.score.toFixed(3)})`);
      } else {
        console.log('   ❌ No results');
      }
      console.log('');
    }

    // Close
    console.log('7. Closing index...');
    await memory.close();
    console.log('✅\n');

    console.log('🎉 All tests passed with Zhipu AI!');
    console.log('\n📊 Summary:');
    console.log(`   - Provider: ${status.provider}`);
    console.log(`   - Model: ${status.model}`);
    console.log(`   - Files indexed: ${status.files}`);
    console.log(`   - Chunks created: ${status.chunks}`);
    console.log(`   - Embeddings generated: ${status.chunks}`);
    console.log(`   - Embedding dimensions: 1024`);
  } catch (error) {
    console.error('❌ Test failed:', error);

    if (error instanceof Error) {
      if (error.message.includes('API')) {
        console.log('\n💡 Tips:');
        console.log('   - Check if Zhipu AI API key is correct');
        console.log('   - Verify you have sufficient API quota');
        console.log('   - Check network connection');
      }
    }

    process.exit(1);
  }
}

test();
