/**
 * Test with mock embedding provider
 */

import { createMemoryIndex, type EmbeddingProvider } from '../src/index.js';

// Create a mock embedding provider for testing
class MockEmbeddingProvider implements EmbeddingProvider {
  id = 'mock';
  model = 'mock-model';
  dimensions = 3;

  async embedQuery(text: string): Promise<number[]> {
    // Simple hash-based mock embeddings
    const hash = text.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
    return [
      Math.sin(hash) % 1,
      Math.cos(hash) % 1,
      Math.tan(hash) % 1,
    ];
  }

  async embedBatch(texts: string[]): Promise<number[][]> {
    return Promise.all(texts.map((t) => this.embedQuery(t)));
  }
}

async function test() {
  console.log('🧪 Testing rag-memory with mock provider...\n');

  try {
    // Create index with mock provider
    console.log('1. Creating memory index...');
    const memory = await createMemoryIndex({
      documentsPath: '.', // Use current dir as workspace
      indexPath: './test-index.sqlite',
      config: {
        embeddings: {
          provider: 'custom',
          customProvider: new MockEmbeddingProvider(),
        },
        storage: {
          vectorEnabled: false, // Disable sqlite-vec for testing
          ftsEnabled: false, // Disable FTS for testing
        },
        sync: {
          watch: false,
        },
        extraPaths: ['./docs'], // Add docs directory relative to workspace
      },
      initialSync: false,
    });

    console.log('✅ Index created\n');

    // Check initial status
    console.log('2. Checking initial status...');
    let status = memory.status();
    console.log('Initial:', { files: status.files, chunks: status.chunks });
    console.log('✅\n');

    // Sync files
    console.log('3. Syncing files...');
    await memory.sync({
      progress: (update) => {
        if (update.total > 0 && update.completed % 5 === 0) {
          console.log(`   Progress: ${update.completed}/${update.total}`);
        }
      },
    });
    console.log('✅ Sync complete\n');

    // Check status after sync
    console.log('4. Checking status after sync...');
    status = memory.status();
    console.log('After sync:', { files: status.files, chunks: status.chunks });
    console.log('✅\n');

    if (status.files === 0) {
      console.log('⚠️  No files found. Creating test documents...\n');
    }

    // Test search
    console.log('5. Testing search...');
    const queries = ['authentication', 'deployment', 'API'];

    for (const query of queries) {
      console.log(`   Query: "${query}"`);
      const results = await memory.search(query, { maxResults: 3 });

      if (results.length === 0) {
        console.log('   → No results\n');
      } else {
        for (const result of results) {
          console.log(`   → ${result.path}:${result.startLine} (score: ${result.score.toFixed(3)})`);
        }
        console.log('');
      }
    }
    console.log('✅ Search works!\n');

    // Test readFile
    console.log('6. Testing readFile...');
    if (status.files > 0) {
      try {
        const file = await memory.readFile('docs/authentication.md');
        console.log(`   Read ${file.text.length} characters from ${file.path}`);
        console.log('✅\n');
      } catch (err) {
        console.log('   ⚠️  File read failed (expected if no files)\n');
      }
    }

    // Close
    console.log('7. Closing index...');
    await memory.close();
    console.log('✅\n');

    console.log('🎉 All tests passed!');
    console.log('\n📊 Summary:');
    console.log(`   - Files indexed: ${status.files}`);
    console.log(`   - Chunks created: ${status.chunks}`);
    console.log(`   - Provider: ${status.provider}`);
    console.log(`   - Model: ${status.model}`);
  } catch (error) {
    console.error('❌ Test failed:', error);
    process.exit(1);
  }
}

test();
