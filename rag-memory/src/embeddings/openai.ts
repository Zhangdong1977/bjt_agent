/**
 * OpenAI embedding provider
 */

import type { EmbeddingProvider } from '../core/types.js';

export interface OpenAIEmbeddingClient {
  baseUrl?: string;
  apiKey: string;
  model?: string;
  headers?: Record<string, string>;
}

const DEFAULT_OPENAI_MODEL = 'text-embedding-3-small';
const DEFAULT_OPENAI_BASE_URL = 'https://api.openai.com/v1';

/**
 * Create OpenAI embedding provider
 */
export async function createOpenAIEmbeddingProvider(
  options: OpenAIEmbeddingClient
): Promise<EmbeddingProvider> {
  const baseUrl = options.baseUrl || DEFAULT_OPENAI_BASE_URL;
  const model = options.model || DEFAULT_OPENAI_MODEL;

  return {
    id: 'openai',
    model,
    dimensions: 1536, // text-embedding-3-small default

    async embedQuery(text: string): Promise<number[]> {
      const response = await fetch(`${baseUrl}/embeddings`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${options.apiKey}`,
          ...options.headers,
        },
        body: JSON.stringify({
          model,
          input: text,
        }),
      });

      if (!response.ok) {
        const error = await response.text();
        throw new Error(`OpenAI API error: ${error}`);
      }

      const data = (await response.json()) as { data: Array<{ embedding: number[] }> };
      return data.data[0].embedding;
    },

    async embedBatch(texts: string[]): Promise<number[][]> {
      const response = await fetch(`${baseUrl}/embeddings`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${options.apiKey}`,
          ...options.headers,
        },
        body: JSON.stringify({
          model,
          input: texts,
        }),
      });

      if (!response.ok) {
        const error = await response.text();
        throw new Error(`OpenAI API error: ${error}`);
      }

      const data = (await response.json()) as { data: Array<{ embedding: number[] }> };
      return data.data.map((item) => item.embedding);
    },
  };
}
