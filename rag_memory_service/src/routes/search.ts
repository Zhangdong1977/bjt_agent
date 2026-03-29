/**
 * Search Route
 * 搜索路由
 *
 * RAG Memory Integration - Task 1.4
 * Implements POST /api/search endpoint
 */

import { Router, Request, Response } from 'express';
import type { MemoryIndex } from 'rag-memory';
import type { IndexManager } from '../indexManager.js';

// Import middleware
import { asyncHandler, createError } from '../middleware/errorHandler.js';

// Extend Request to include indexManager
declare global {
  namespace Express {
    interface Request {
      indexManager?: IndexManager;
    }
  }
}

const router = Router();

/**
 * Search request body
 */
interface SearchRequestBody {
  query: string;
  limit?: number;
  options?: {
    minScore?: number;
  };
}

/**
 * Search response body
 */
interface SearchResponseBody {
  results: Array<{
    path: string;
    startLine: number;
    endLine: number;
    score: number;
    snippet: string;
  }>;
  queryTime: number;
  totalResults: number;
}

/**
 * Simple logger
 */
const log = {
  info: (msg: string, ...args: any[]) => console.log(`[rag-memory:search] ${msg}`, ...args),
  warn: (msg: string, ...args: any[]) => console.warn(`[rag-memory:search] ${msg}`, ...args),
  error: (msg: string, ...args: any[]) => console.error(`[rag-memory:search] ${msg}`, ...args),
};

/**
 * POST /api/search
 * Search knowledge base using rag-memory
 *
 * Request body:
 * - query: Search query text (required, max 500 chars)
 * - limit: Maximum results to return (optional, default 10, max 50)
 * - options.minScore: Minimum relevance score 0-1 (optional)
 *
 * Response:
 * - results: Array of search results with path, line numbers, score, snippet
 * - queryTime: Search execution time in milliseconds
 * - totalResults: Number of results returned
 */
router.post(
  '/search',
  asyncHandler(async (req: Request, res: Response) => {
    const { query, limit = 10, options = {} }: SearchRequestBody = req.body;

    // Read X-User-ID header
    const userId = req.headers['x-user-id'] as string;

    if (!userId) {
      log.warn('Missing X-User-ID header');
      throw createError('X-User-ID header is required', 400, 'missing_user_id');
    }

    // Validate request - Requirement 1.1, 2.1
    if (!query || typeof query !== 'string') {
      log.warn('Invalid query: missing or not a string');
      throw createError('Query is required and must be a string', 400, 'invalid_query');
    }

    const trimmedQuery = query.trim();
    if (trimmedQuery.length === 0) {
      log.warn('Empty query received');
      throw createError('Query cannot be empty', 400, 'empty_query');
    }

    if (query.length > 500) {
      log.warn('Query too long:', { length: query.length });
      throw createError('Query is too long (max 500 characters)', 400, 'query_too_long');
    }

    // Clamp limit between 1 and 50
    const maxResults = Math.min(Math.max(limit, 1), 50);

    // Get index manager
    const manager = req.indexManager;
    if (!manager) {
      log.error('Index manager not initialized');
      throw createError('Index manager not initialized', 503, 'service_unavailable');
    }

    // Get user's memory index
    const memory = await manager.getIndex(userId);

    // Validate minScore if provided
    let minScore = options.minScore;
    if (minScore !== undefined) {
      if (typeof minScore !== 'number' || minScore < 0 || minScore > 1) {
        log.warn('Invalid minScore:', { minScore });
        throw createError('minScore must be a number between 0 and 1', 400, 'invalid_min_score');
      }
    }

    // Perform search - Requirement 1.1
    const startTime = Date.now();
    const results = await memory.search(trimmedQuery, {
      maxResults,
      minScore,
    });
    const queryTime = Date.now() - startTime;

    // Log search results - Requirement 1.6
    log.info('Search completed', {
      userId,
      query: trimmedQuery.substring(0, 50),
      resultsCount: results.length,
      queryTime,
    });

    // Format response
    const response: SearchResponseBody = {
      results: results.map((r) => ({
        path: r.path,
        startLine: r.startLine,
        endLine: r.endLine,
        score: r.score,
        snippet: r.snippet,
      })),
      queryTime,
      totalResults: results.length,
    };

    res.json(response);
  })
);

export default router;
