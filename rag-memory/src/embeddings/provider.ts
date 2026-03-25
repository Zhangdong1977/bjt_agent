/**
 * Embedding provider factory and types
 */

import type {
  EmbeddingProvider,
  EmbeddingConfig,
  EmbeddingProviderType,
} from '../core/types.js';
import { createOpenAIEmbeddingProvider } from './openai.js';
import { createGeminiEmbeddingProvider } from './gemini.js';
import { createZhipuEmbeddingProvider } from './zhipu.js';

/**
 * Create an embedding provider based on configuration
 */
export async function createEmbeddingProvider(
  config: EmbeddingConfig
): Promise<EmbeddingProvider> {
  // Custom provider takes precedence
  if (config.provider === 'custom' && config.customProvider) {
    return config.customProvider;
  }

  // OpenAI
  if (config.provider === 'openai') {
    if (!config.remote?.apiKey) {
      throw new Error('OpenAI API key is required');
    }
    return await createOpenAIEmbeddingProvider({
      baseUrl: config.remote.baseUrl || undefined,
      apiKey: config.remote.apiKey,
      model: config.model || 'text-embedding-3-small',
      headers: config.remote.headers || {},
    });
  }

  // Gemini
  if (config.provider === 'gemini') {
    if (!config.remote?.apiKey) {
      throw new Error('Gemini API key is required');
    }
    return await createGeminiEmbeddingProvider({
      baseUrl: config.remote.baseUrl || undefined,
      apiKey: config.remote.apiKey,
      model: config.model || 'gemini-embedding-001',
      headers: config.remote.headers || {},
    });
  }

  // Zhipu AI
  if (config.provider === 'zhipu') {
    if (!config.remote?.apiKey) {
      throw new Error('Zhipu AI API key is required');
    }
    return await createZhipuEmbeddingProvider({
      apiKey: config.remote.apiKey,
      model: config.model,
      dimensions: config.remote.dimensions,
      headers: config.remote.headers,
    });
  }

  // Local
  if (config.provider === 'local') {
    throw new Error(
      'Local embeddings require node-llama-cpp. ' +
        'Please install it as a peer dependency or use a different provider.'
    );
  }

  throw new Error(`Unknown embedding provider: ${config.provider}`);
}

/**
 * Check if an embedding provider type requires an API key
 */
export function requiresApiKey(provider: EmbeddingProviderType): boolean {
  return provider === 'openai' || provider === 'gemini' || provider === 'zhipu';
}
