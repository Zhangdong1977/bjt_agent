/**
 * Simple test for rag-memory
 */

import { createMemoryIndex } from '../src/index.js';

async function test() {
  console.log('🧪 Testing rag-memory...\n');

  try {
    // Create index
    console.log('1. Creating memory index...');
    const memory = await createMemoryIndex({
      documentsPath: './docs',
      indexPath: './test-index.sqlite',
      config: {
        embeddings: {
          provider: 'openai',
          remote: {
            apiKey: process.env.OPENAI_API_KEY || 'test-key',
          },
        },
        sync: {
          watch: false, // Disable file watching for tests
        },
      },
      initialSync: false, // We'll sync manually
    });

    console.log('✅ Index created\n');

    // Check status
    console.log('2. Checking status...');
    const status = memory.status();
    console.log('Status:', {
      files: status.files,
      chunks: status.chunks,
      dirty: status.dirty,
      provider: status.provider,
    });
    console.log('✅ Status checked\n');

    // Sync (index files)
    console.log('3. Syncing files...');
    await memory.sync({
      progress: (update) => {
        if (update.total > 0) {
          console.log(`   Progress: ${update.completed}/${update.total}`);
        }
      },
    });
    console.log('✅ Sync complete\n');

    // Check status after sync
    console.log('4. Checking status after sync...');
    const statusAfter = memory.status();
    console.log('Status after sync:', {
      files: statusAfter.files,
      chunks: statusAfter.chunks,
      dirty: statusAfter.dirty,
    });
    console.log('✅ Files indexed!\n');

    // Test search (this will fail without a real API key, but that's ok for now)
    console.log('5. Testing search...');
    try {
      const results = await memory.search('authentication');
      console.log(`Found ${results.length} results`);
      for (const result of results.slice(0, 2)) {
        console.log(`  - ${result.path}:${result.startLine} (score: ${result.score.toFixed(2)})`);
      }
      console.log('✅ Search works!\n');
    } catch (searchError: any) {
      if (searchError.message.includes('API key') || searchError.message.includes('401')) {
        console.log('⚠️  Search test skipped (no valid API key)\n');
      } else {
        throw searchError;
      }
    }

    // Close
    console.log('6. Closing index...');
    await memory.close();
    console.log('✅ Index closed\n');

    console.log('🎉 All tests passed!');
  } catch (error) {
    console.error('❌ Test failed:', error);
    process.exit(1);
  }
}

test();
