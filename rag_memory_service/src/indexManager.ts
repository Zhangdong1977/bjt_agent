/**
 * Index Manager
 * 多租户 MemoryIndex 管理器
 */

import { createMemoryIndex, MemoryIndex } from 'rag-memory';
import path from 'node:path';

export interface IndexManagerOptions {
  documentsBasePath: string;  // e.g., /workspace/knowledge
}

export interface IndexStatus {
  status: 'ready' | 'indexing' | 'error';
  files: number;
  chunks: number;
  provider: string;
  model: string;
}

export class IndexManager {
  private indices: Map<string, MemoryIndex> = new Map();
  private documentsBasePath: string;

  constructor(options: IndexManagerOptions) {
    this.documentsBasePath = options.documentsBasePath;
  }

  /**
   * Get or create MemoryIndex for a user
   */
  async getIndex(userId: string): Promise<MemoryIndex> {
    // Return cached index if exists
    if (this.indices.has(userId)) {
      return this.indices.get(userId)!;
    }

    // Create new index for user
    const userPath = path.join(this.documentsBasePath, userId);
    const indexPath = path.join(userPath, 'data', 'memory.sqlite');

    const memory = await createMemoryIndex({
      documentsPath: userPath,
      indexPath: indexPath,
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
        },
        search: {
          maxResults: 50,
          minScore: 0.0,
          hybrid: {
            enabled: true,
            vectorWeight: 0.7,
            textWeight: 0.3,
            candidateMultiplier: 2.0,
          },
        },
        extraPaths: [userPath],
      },
      initialSync: false,  // Don't sync on creation
    });

    this.indices.set(userId, memory);
    return memory;
  }

  /**
   * Get index status for a user
   */
  async getStatus(userId: string): Promise<IndexStatus | null> {
    const memory = this.indices.get(userId);
    if (!memory) {
      return null;
    }

    const status = memory.status();
    return {
      status: status.dirty ? 'indexing' : 'ready',
      files: status.files,
      chunks: status.chunks,
      provider: status.provider,
      model: status.model,
    };
  }

  /**
   * Close and remove index for a user
   */
  async closeIndex(userId: string): Promise<void> {
    const memory = this.indices.get(userId);
    if (memory) {
      await memory.close();
      this.indices.delete(userId);
    }
  }

  /**
   * Close all indices
   */
  async closeAll(): Promise<void> {
    for (const [userId, memory] of this.indices) {
      await memory.close();
    }
    this.indices.clear();
  }
}