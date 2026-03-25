/**
 * Status Route
 * 状态路由
 */

import { Router, Request, Response } from 'express';
import { asyncHandler, createError } from '../middleware/errorHandler.js';

// Import types from rag-memory
import type { MemoryIndex, IndexStatus } from 'rag-memory';

// Extend Request to include memory instance
declare global {
  namespace Express {
    interface Request {
      memory?: MemoryIndex;
    }
  }
}

const router = Router();

interface StatusResponseBody {
  status: 'ready' | 'indexing' | 'error';
  files: number;
  chunks: number;
  provider: string;
  model: string;
  lastSync: string;
}

/**
 * GET /api/status
 * Get service status
 */
router.get(
  '/status',
  asyncHandler(async (req: Request, res: Response) => {
    // Check if memory instance is available
    if (!req.memory) {
      throw createError('Memory index not initialized', 503, 'service_unavailable');
    }

    const memoryStatus: IndexStatus = req.memory.status();

    // Map internal status to API status
    let apiStatus: 'ready' | 'indexing' | 'error' = 'ready';
    if (memoryStatus.dirty) {
      apiStatus = 'indexing';
    }

    const response: StatusResponseBody = {
      status: apiStatus,
      files: memoryStatus.files,
      chunks: memoryStatus.chunks,
      provider: memoryStatus.provider,
      model: memoryStatus.model,
      lastSync: new Date().toISOString(), // rag-memory doesn't track last sync time
    };

    res.json(response);
  })
);

/**
 * GET /api/health
 * Health check endpoint
 */
router.get('/health', (req: Request, res: Response) => {
  const isMemoryReady = !!req.memory;

  res.json({
    status: isMemoryReady ? 'ok' : 'degraded',
    timestamp: new Date().toISOString(),
    memory: isMemoryReady ? 'ready' : 'not_initialized',
  });
});

export default router;
