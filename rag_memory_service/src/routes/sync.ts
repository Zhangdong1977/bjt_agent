/**
 * Sync Route
 * 同步路由
 *
 * RAG Memory Integration - Task 1.5
 * Implements manual sync with progress tracking and statistics
 */

import { Router, Request, Response } from 'express';
import { asyncHandler, createError } from '../middleware/errorHandler.js';

// Import types from rag-memory
import type { MemoryIndex, IndexStatus, SyncOptions } from 'rag-memory';

// Extend Request to include memory instance
declare global {
  namespace Express {
    interface Request {
      memory?: MemoryIndex;
    }
  }
}

const router = Router();

interface SyncRequestBody {
  force?: boolean;
}

interface SyncResponseBody {
  filesProcessed: number;
  chunksCreated: number;
  duration: number;
  errors: string[];
}

/**
 * POST /api/sync
 * Trigger index synchronization
 *
 * This endpoint triggers a manual sync of the knowledge base index.
 * It tracks statistics before and after sync to provide meaningful feedback.
 */
router.post(
  '/sync',
  asyncHandler(async (req: Request, res: Response) => {
    const { force = false }: SyncRequestBody = req.body;

    // Check if memory instance is available
    if (!req.memory) {
      throw createError('Memory index not initialized', 503, 'service_unavailable');
    }

    // Get status before sync for statistics tracking
    const statusBefore: IndexStatus = req.memory.status();
    const errors: string[] = [];

    // Perform sync with progress tracking
    const startTime = Date.now();
    try {
      await req.memory.sync({
        force,
        progress: (update) => {
          // Log progress updates
          console.log(`[rag-memory:sync] Progress: ${update.completed}/${update.total}${update.label ? ` - ${update.label}` : ''}`);
        },
      });
    } catch (err) {
      // Collect errors during sync
      const errorMsg = err instanceof Error ? err.message : String(err);
      errors.push(errorMsg);
      console.error('[rag-memory:sync] Error:', errorMsg);
    }

    const duration = Date.now() - startTime;

    // Get status after sync to calculate statistics
    const statusAfter: IndexStatus = req.memory.status();

    const response: SyncResponseBody = {
      filesProcessed: statusAfter.files,
      chunksCreated: statusAfter.chunks,
      duration,
      errors,
    };

    res.json(response);
  })
);

export default router;
