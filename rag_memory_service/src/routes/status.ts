/**
 * Status Route
 * 状态路由
 */

import { Router, Request, Response } from 'express';
import { asyncHandler, createError } from '../middleware/errorHandler.js';

// Import types from rag-memory
import type { IndexManager } from 'rag-memory';

// Extend Request to include index manager instance
declare global {
  namespace Express {
    interface Request {
      indexManager?: IndexManager;
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
 * Get user index status
 */
router.get(
  '/status',
  asyncHandler(async (req: Request, res: Response) => {
    const userId = req.headers['x-user-id'] as string;

    if (!userId) {
      throw createError('X-User-ID header is required', 400, 'missing_user_id');
    }

    const manager = req.indexManager;
    if (!manager) {
      throw createError('Index manager not initialized', 503, 'service_unavailable');
    }

    const status = await manager.getStatus(userId);

    if (!status) {
      // User has no index yet - return empty status
      res.json({
        status: 'ready',
        files: 0,
        chunks: 0,
        provider: 'zhipu',
        model: 'embedding-3',
        lastSync: new Date().toISOString(),
      });
      return;
    }

    res.json({
      ...status,
      lastSync: new Date().toISOString(),
    });
  })
);

/**
 * GET /api/health
 * Health check endpoint
 */
router.get('/health', (req: Request, res: Response) => {
  const isIndexManagerReady = !!req.indexManager;

  res.json({
    status: isIndexManagerReady ? 'ok' : 'degraded',
    timestamp: new Date().toISOString(),
    indexManager: isIndexManagerReady ? 'ready' : 'not_initialized',
  });
});

export default router;
