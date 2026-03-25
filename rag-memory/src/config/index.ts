/**
 * Default configuration and config resolution
 */

import type { MemoryConfig } from '../core/types.js';
import path from 'node:path';
import os from 'node:os';

/**
 * Default configuration values
 */
export const DEFAULT_CONFIG: Partial<MemoryConfig> = {
  storage: {
    path: path.join(os.homedir(), '.rag-memory', 'index.sqlite'),
    workspaceDir: path.join(os.homedir(), '.rag-memory', 'documents'),
    vectorEnabled: true,
    ftsEnabled: true,
  },
  embeddings: {
    provider: 'openai',
    model: 'text-embedding-3-small',
  },
  chunking: {
    tokens: 400,
    overlap: 80,
  },
  search: {
    maxResults: 10,
    minScore: 0.35,
    hybrid: {
      enabled: true,
      vectorWeight: 0.7,
      textWeight: 0.3,
      candidateMultiplier: 4,
    },
  },
  sync: {
    onSearch: true,
    watch: true,
    watchDebounceMs: 1500,
    intervalMinutes: 0,
  },
  cache: {
    enabled: true,
    maxEntries: 50000,
  },
  sources: ['memory'],
};

/**
 * Resolve and merge configuration with defaults
 */
export function resolveConfig(
  userConfig?: Partial<MemoryConfig>
): MemoryConfig {
  const merged: MemoryConfig = {
    storage: {
      ...DEFAULT_CONFIG.storage!,
      ...userConfig?.storage,
    },
    embeddings: {
      ...DEFAULT_CONFIG.embeddings!,
      ...userConfig?.embeddings,
    },
    chunking: {
      ...DEFAULT_CONFIG.chunking!,
      ...userConfig?.chunking,
    },
    search: {
      ...DEFAULT_CONFIG.search!,
      ...userConfig?.search,
      hybrid: {
        ...DEFAULT_CONFIG.search!.hybrid,
        ...userConfig?.search?.hybrid,
      },
    },
    sync: {
      ...DEFAULT_CONFIG.sync!,
      ...userConfig?.sync,
    },
    cache: {
      ...DEFAULT_CONFIG.cache!,
      ...userConfig?.cache,
    },
    sources: userConfig?.sources || DEFAULT_CONFIG.sources,
    extraPaths: userConfig?.extraPaths,
  };

  // Normalize weights to sum to 1
  const totalWeight = merged.search.hybrid.vectorWeight + merged.search.hybrid.textWeight;
  if (totalWeight !== 1) {
    merged.search.hybrid.vectorWeight = merged.search.hybrid.vectorWeight / totalWeight;
    merged.search.hybrid.textWeight = merged.search.hybrid.textWeight / totalWeight;
  }

  return merged;
}

/**
 * Validate configuration
 */
export function validateConfig(config: MemoryConfig): void {
  if (config.storage.vectorEnabled && !config.storage.ftsEnabled) {
    console.warn('Vector search is enabled but FTS is disabled. Hybrid search may not work optimally.');
  }

  if (config.search.maxResults < 1) {
    throw new Error('search.maxResults must be at least 1');
  }

  if (config.search.minScore < 0 || config.search.minScore > 1) {
    throw new Error('search.minScore must be between 0 and 1');
  }

  if (config.chunking.tokens < 32) {
    throw new Error('chunking.tokens must be at least 32');
  }

  if (config.chunking.overlap < 0) {
    throw new Error('chunking.overlap cannot be negative');
  }

  if (config.chunking.overlap >= config.chunking.tokens) {
    throw new Error('chunking.overlap must be less than chunking.tokens');
  }
}
