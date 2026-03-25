/**
 * Basic usage example for rag-memory
 *
 * Run with: npm run example:basic
 */

import { createMemoryIndex } from '../src/index.js';

async function main() {
  console.log('🚀 Creating memory index...\n');

  // Create memory index
  const memory = await createMemoryIndex({
    documentsPath: './docs',
    config: {
      embeddings: {
        provider: 'openai',
        remote: {
          apiKey: process.env.OPENAI_API_KEY || '',
        },
      },
      search: {
        maxResults: 5,
        minScore: 0.3,
      },
    },
  });

  console.log('✅ Index created and synced!\n');
  console.log('📊 Status:', memory.status());

  // Example searches
  const queries = [
    'authentication',
    'API usage',
    'configuration',
  ];

  for (const query of queries) {
    console.log(`\n🔍 Searching for: "${query}"`);
    console.log('─'.repeat(60));

    try {
      const results = await memory.search(query);

      if (results.length === 0) {
        console.log('No results found');
        continue;
      }

      for (const result of results) {
        console.log(`\n  📄 ${result.path}:${result.startLine}-${result.endLine}`);
        console.log(`  📊 Score: ${(result.score * 100).toFixed(1)}%`);
        console.log(`  📝 Snippet: ${result.snippet.slice(0, 150)}...`);
      }
    } catch (error) {
      console.error('Search error:', error);
    }
  }

  // Close index
  await memory.close();
  console.log('\n✅ Index closed');
}

main().catch(console.error);
