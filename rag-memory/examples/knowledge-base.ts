/**
 * Knowledge base example for rag-memory
 *
 * Run with: npm run example:knowledge
 */

import { createMemoryIndex, type MemoryIndex } from '../src/index.js';

class KnowledgeBase {
  private memory: MemoryIndex;

  constructor(memory: MemoryIndex) {
    this.memory = memory;
  }

  /**
   * Search the knowledge base
   */
  async search(query: string, options?: { maxResults?: number }) {
    return await this.memory.search(query, {
      maxResults: options?.maxResults || 3,
    });
  }

  /**
   * Get relevant context for a question
   */
  async getContext(question: string): Promise<string> {
    const results = await this.search(question, { maxResults: 3 });

    if (results.length === 0) {
      return 'No relevant information found in the knowledge base.';
    }

    const contexts = results.map((r) => {
      return `From ${r.path}:\n${r.snippet}`;
    });

    return contexts.join('\n\n');
  }

  /**
   * Answer a question using the knowledge base
   */
  async answer(question: string): Promise<string> {
    const context = await this.getContext(question);

    // In a real application, you would send this to an LLM
    return `Based on the knowledge base:\n\n${context}`;
  }

  /**
   * Close the knowledge base
   */
  async close() {
    await this.memory.close();
  }
}

async function main() {
  console.log('📚 Creating knowledge base...\n');

  const memory = await createMemoryIndex({
    documentsPath: './kb',
    config: {
      embeddings: {
        provider: 'openai',
        remote: {
          apiKey: process.env.OPENAI_API_KEY || '',
        },
      },
      chunking: {
        tokens: 500,
        overlap: 100,
      },
      search: {
        maxResults: 5,
        hybrid: {
          vectorWeight: 0.8, // More weight on semantic similarity
          textWeight: 0.2,
        },
      },
    },
  });

  const kb = new KnowledgeBase(memory);

  console.log('✅ Knowledge base ready!\n');

  // Example questions
  const questions = [
    'How do I reset my password?',
    'What are the pricing plans?',
    'How do I contact support?',
  ];

  for (const question of questions) {
    console.log(`\n❓ Question: ${question}`);
    console.log('─'.repeat(60));

    try {
      const answer = await kb.answer(question);
      console.log(answer.slice(0, 300) + '...');
    } catch (error) {
      console.error('Error:', error);
    }
  }

  await kb.close();
  console.log('\n✅ Knowledge base closed');
}

main().catch(console.error);
