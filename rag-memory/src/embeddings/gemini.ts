/**
 * Google Gemini embedding provider
 */

import type { EmbeddingProvider } from '../core/types.js';

export interface GeminiEmbeddingClient {
  baseUrl?: string;
  apiKey: string;
  model?: string;
  headers?: Record<string, string>;
}

const DEFAULT_GEMINI_MODEL = 'gemini-embedding-001';
const DEFAULT_GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta';

/**
 * Create Gemini embedding provider
 */
export async function createGeminiEmbeddingProvider(
  options: GeminiEmbeddingClient
): Promise<EmbeddingProvider> {
  const baseUrl = options.baseUrl || DEFAULT_GEMINI_BASE_URL;
  const model = options.model || DEFAULT_GEMINI_MODEL;

  return {
    id: 'gemini',
    model,
    dimensions: 768, // gemini-embedding-001 default

    async embedQuery(text: string): Promise<number[]> {
      const url = `${baseUrl}/models/${model}:embedContent?key=${options.apiKey}`;
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...options.headers,
        },
        body: JSON.stringify({
          content: {
            parts: [{ text }],
          },
          taskType: 'RETRIEVAL_DOCUMENT',
        }),
      });

      if (!response.ok) {
        const error = await response.text();
        throw new Error(`Gemini API error: ${error}`);
      }

      const data = (await response.json()) as { embedding: { values: number[] } };
      return data.embedding.values;
    },

    async embedBatch(texts: string[]): Promise<number[][]> {
      // Gemini doesn't have a true batch endpoint, so we parallelize
      const provider = this as EmbeddingProvider;
      return Promise.all(texts.map((text) => provider.embedQuery(text)));
    },
  };
}
