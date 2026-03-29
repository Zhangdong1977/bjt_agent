/**
 * IndexManager unit tests
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { IndexManager } from './indexManager';

// Mock the rag-memory module
vi.mock('rag-memory', () => {
  const mockMemoryIndex = vi.fn(() => ({
    search: vi.fn(),
    sync: vi.fn(),
    status: vi.fn(() => ({
      files: 10,
      chunks: 100,
      dirty: false,
      provider: 'zhipu',
      model: 'embedding-3',
    })),
    readFile: vi.fn(),
    close: vi.fn().mockResolvedValue(undefined),
  }));

  return {
    createMemoryIndex: vi.fn(() => mockMemoryIndex()),
  };
});

describe('IndexManager', () => {
  const basePath = '/tmp/test-knowledge';

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(async () => {
    // Clean up any open indices
  });

  describe('getIndex', () => {
    it('should return cached index for same user', async () => {
      const manager = new IndexManager({ documentsBasePath: basePath });

      const index1 = await manager.getIndex('user1');
      const index2 = await manager.getIndex('user1');

      expect(index1).toBe(index2);
    });

    it('should return different indices for different users', async () => {
      const manager = new IndexManager({ documentsBasePath: basePath });

      const index1 = await manager.getIndex('user1');
      const index2 = await manager.getIndex('user2');

      expect(index1).not.toBe(index2);
    });

    it('should create index with correct paths', async () => {
      const manager = new IndexManager({ documentsBasePath: basePath });

      await manager.getIndex('testuser');

      // The createMemoryIndex should have been called with user-specific paths
      const { createMemoryIndex } = await import('rag-memory');
      expect(createMemoryIndex).toHaveBeenCalledWith(
        expect.objectContaining({
          documentsPath: expect.stringContaining('testuser'),
          indexPath: expect.stringContaining('testuser'),
          initialSync: false,
        })
      );
    });
  });

  describe('getStatus', () => {
    it('should return null for unknown user', async () => {
      const manager = new IndexManager({ documentsBasePath: basePath });

      const status = await manager.getStatus('unknownuser');

      expect(status).toBeNull();
    });

    it('should return status for existing user', async () => {
      const manager = new IndexManager({ documentsBasePath: basePath });

      await manager.getIndex('user1');
      const status = await manager.getStatus('user1');

      expect(status).not.toBeNull();
      expect(status).toHaveProperty('status', 'ready');
      expect(status).toHaveProperty('files', 10);
      expect(status).toHaveProperty('chunks', 100);
      expect(status).toHaveProperty('provider', 'zhipu');
      expect(status).toHaveProperty('model', 'embedding-3');
    });

    it('should return indexing status when dirty', async () => {
      // Mock a dirty index
      const { createMemoryIndex } = await import('rag-memory');
      (createMemoryIndex as any).mockImplementationOnce(() => ({
        search: vi.fn(),
        sync: vi.fn(),
        status: vi.fn(() => ({
          files: 5,
          chunks: 50,
          dirty: true,
          provider: 'zhipu',
          model: 'embedding-3',
        })),
        readFile: vi.fn(),
        close: vi.fn().mockResolvedValue(undefined),
      }));

      const manager = new IndexManager({ documentsBasePath: basePath });

      await manager.getIndex('dirtyuser');
      const status = await manager.getStatus('dirtyuser');

      expect(status).toHaveProperty('status', 'indexing');
    });
  });

  describe('closeIndex', () => {
    it('should close and remove index for user', async () => {
      const manager = new IndexManager({ documentsBasePath: basePath });

      await manager.getIndex('user1');
      await manager.closeIndex('user1');

      // After closing, getStatus should return null
      const status = await manager.getStatus('user1');
      expect(status).toBeNull();
    });

    it('should handle closing non-existent index gracefully', async () => {
      const manager = new IndexManager({ documentsBasePath: basePath });

      // Should not throw
      await expect(manager.closeIndex('nonexistent')).resolves.toBeUndefined();
    });
  });

  describe('closeAll', () => {
    it('should close all indices', async () => {
      const manager = new IndexManager({ documentsBasePath: basePath });

      await manager.getIndex('user1');
      await manager.getIndex('user2');
      await manager.getIndex('user3');

      await manager.closeAll();

      // All indices should be closed
      const status1 = await manager.getStatus('user1');
      const status2 = await manager.getStatus('user2');
      const status3 = await manager.getStatus('user3');

      expect(status1).toBeNull();
      expect(status2).toBeNull();
      expect(status3).toBeNull();
    });

    it('should handle empty manager gracefully', async () => {
      const manager = new IndexManager({ documentsBasePath: basePath });

      // Should not throw
      await expect(manager.closeAll()).resolves.toBeUndefined();
    });
  });
});