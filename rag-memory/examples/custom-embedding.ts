/**
 * Custom embedding provider example
 *
 * Run with: tsx examples/custom-embedding.ts
 */

import { createMemoryIndex, type EmbeddingProvider } from '../src/index.js';

/**
 * Custom embedding provider using a hypothetical API
 */
class CustomEmbeddingProvider implements EmbeddingProvider {
  id = 'custom';
  model = 'my-model';
  dimensions = 768;

  constructor(private apiKey: string) {}

  async embedQuery(text: string): Promise<number[]> {
    const response = await fetch('https://api.example.com/embeddings', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.apiKey}`,
      },
      body: JSON.stringify({ text }),
    });

    if (!response.ok) {
      throw new Error(`Custom API error: ${response.statusText}`);
    }

    const data = await response.json();
    return data.embedding;
  }

  async embedBatch(texts: string[]): Promise<number[][]> {
    // For APIs that don't support batching, parallelize
    return Promise.all(texts.map((text) => this.embedQuery(text)));
  }
}

async function main() {
  console.log('🔧 Creating memory index with custom embedding...\n');

  const memory = await createMemoryIndex({
    documentsPath: './docs',
    config: {
      embeddings: {
        provider: 'custom',
        customProvider: new CustomEmbeddingProvider(process.env.CUSTOM_API_KEY || ''),
      },
    },
  });

  console.log('✅ Index created with custom embedding!\n');

  const results = await memory.search('test query');
  console.log('Results:', results.length);

  await memory.close();
}

main().catch(console.error);
