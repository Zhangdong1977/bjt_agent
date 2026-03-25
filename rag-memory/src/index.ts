/**
 * RAG Memory - Hybrid RAG memory system with vector + keyword search
 *
 * @example
 * ```typescript
 * import { createMemoryIndex } from 'rag-memory';
 *
 * const memory = await createMemoryIndex({
 *   documentsPath: './docs',
 *   config: {
 *     embeddings: {
 *       provider: 'openai',
 *       model: 'text-embedding-3-small',
 *       remote: {
 *         apiKey: process.env.OPENAI_API_KEY,
 *       },
 *     },
 *   },
 * });
 *
 * const results = await memory.search('How to deploy?');
 * await memory.close();
 * ```
 */

// Export all types
export * from './core/types.js';

// Export core manager
export { MemoryIndexManager } from './core/manager.js';

// Export embedding providers
export { createOpenAIEmbeddingProvider } from './embeddings/openai.js';
export { createGeminiEmbeddingProvider } from './embeddings/gemini.js';
export { createZhipuEmbeddingProvider } from './embeddings/zhipu.js';
export type { OpenAIEmbeddingClient } from './embeddings/openai.js';
export type { GeminiEmbeddingClient } from './embeddings/gemini.js';
export type { ZhipuEmbeddingClient } from './embeddings/zhipu.js';

// Export configuration
export { resolveConfig, validateConfig, DEFAULT_CONFIG } from './config/index.js';

// Main factory function
import type { MemoryConfig, MemoryIndex } from './core/types.js';
import { resolveConfig, validateConfig } from './config/index.js';
import { MemoryIndexManager } from './core/manager.js';
import { createEmbeddingProvider } from './embeddings/provider.js';
import path from 'node:path';

export interface CreateMemoryIndexOptions {
  /**
   * Path to documents directory
   * @default ~/.rag-memory/documents
   */
  documentsPath?: string;

  /**
   * Path to index database
   * @default ~/.rag-memory/index.sqlite
   */
  indexPath?: string;

  /**
   * Partial configuration to override defaults
   */
  config?: Partial<MemoryConfig>;

  /**
   * Whether to perform initial sync
   * @default true
   */
  initialSync?: boolean;
}

/**
 * Create a new memory index
 *
 * @param options - Index creation options
 * @returns Memory index instance
 *
 * @example
 * ```typescript
 * const memory = await createMemoryIndex({
 *   documentsPath: './docs',
 *   config: {
 *     embeddings: {
 *       provider: 'openai',
 *       remote: { apiKey: process.env.OPENAI_API_KEY },
 *     },
 *   },
 * });
 * ```
 */
export async function createMemoryIndex(
  options: CreateMemoryIndexOptions = {}
): Promise<MemoryIndex> {
  // Resolve configuration
  const config = resolveConfig(options.config);

  // Override paths if provided
  if (options.documentsPath) {
    config.storage.workspaceDir = path.resolve(options.documentsPath);
  }
  if (options.indexPath) {
    config.storage.path = path.resolve(options.indexPath);
  }

  // Validate configuration
  validateConfig(config);

  // Create embedding provider
  const provider = await createEmbeddingProvider(config.embeddings);

  // Create manager
  const manager = new MemoryIndexManager(config, provider);

  // Perform initial sync
  if (options.initialSync !== false) {
    await manager.sync();
  }

  return manager;
}
