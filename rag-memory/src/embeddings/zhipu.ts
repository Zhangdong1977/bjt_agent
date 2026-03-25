/**
 * Zhipu AI (智谱AI) embedding provider
 */

import type { EmbeddingProvider } from '../core/types.js';

export interface ZhipuEmbeddingClient {
  apiKey: string;
  baseUrl?: string;
  model?: string;
  dimensions?: number;
  headers?: Record<string, string>;
}

const DEFAULT_ZHIPU_MODEL = 'embedding-3';
const DEFAULT_ZHIPU_BASE_URL = 'https://open.bigmodel.cn/api/paas/v4/embeddings';
const DEFAULT_ZHIPU_DIMENSIONS = 1024;

/**
 * Create Zhipu AI embedding provider
 */
export async function createZhipuEmbeddingProvider(
  options: ZhipuEmbeddingClient
): Promise<EmbeddingProvider> {
  const baseUrl = options.baseUrl || DEFAULT_ZHIPU_BASE_URL;
  const model = options.model || DEFAULT_ZHIPU_MODEL;
  const dimensions = options.dimensions || DEFAULT_ZHIPU_DIMENSIONS;

  return {
    id: 'zhipu',
    model,
    dimensions,

    async embedQuery(text: string): Promise<number[]> {
      const requestBody: Record<string, any> = {
        model,
        input: text,
      };

      // Add dimensions if specified
      if (options.dimensions) {
        requestBody.dimensions = options.dimensions;
      }

      const response = await fetch(baseUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${options.apiKey}`,
          ...options.headers,
        },
        body: JSON.stringify(requestBody),
      });

      if (!response.ok) {
        const error = await response.text();
        throw new Error(`Zhipu AI API error: ${error}`);
      }

      const data = (await response.json()) as {
        data: Array<{ embedding: number[]; index: number }>;
        model: string;
        usage: { prompt_tokens: number; total_tokens: number };
      };

      if (!data.data || data.data.length === 0) {
        throw new Error('Zhipu AI returned empty embedding');
      }

      return data.data[0].embedding;
    },

    async embedBatch(texts: string[]): Promise<number[][]> {
      // Zhipu AI supports batch input
      const requestBody: Record<string, any> = {
        model,
        input: texts,
      };

      // Add dimensions if specified
      if (options.dimensions) {
        requestBody.dimensions = options.dimensions;
      }

      const response = await fetch(baseUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${options.apiKey}`,
          ...options.headers,
        },
        body: JSON.stringify(requestBody),
      });

      if (!response.ok) {
        const error = await response.text();
        throw new Error(`Zhipu AI API error: ${error}`);
      }

      const data = (await response.json()) as {
        data: Array<{ embedding: number[]; index: number }>;
        model: string;
        usage: { prompt_tokens: number; total_tokens: number };
      };

      if (!data.data || data.data.length === 0) {
        throw new Error('Zhipu AI returned empty embeddings');
      }

      // Sort by index to ensure correct order
      const sorted = data.data.sort((a, b) => a.index - b.index);
      return sorted.map((item) => item.embedding);
    },
  };
}
