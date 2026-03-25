/**
 * ReadFile Route
 * 文件读取路由
 *
 * RAG Memory Integration - Task 1.5
 * Implements file reading from the knowledge base
 */

import { Router, Request, Response } from 'express';
import { asyncHandler, createError } from '../middleware/errorHandler.js';
import path from 'node:path';

// Import types from rag-memory
import type { MemoryIndex, ReadFileOptions } from 'rag-memory';

// Extend Request to include memory instance
declare global {
  namespace Express {
    interface Request {
      memory?: MemoryIndex;
    }
  }
}

const router = Router();

interface ReadFileResponseBody {
  content: string;
  path: string;
}

/**
 * GET /api/readfile
 * Read file content from the knowledge base
 *
 * Query parameters:
 * - path: File path (required)
 * - lineStart: Start line number (optional, 1-based)
 * - lines: Number of lines to read (optional)
 */
router.get(
  '/readfile',
  asyncHandler(async (req: Request, res: Response) => {
    const filePath = req.query.path as string;
    const lineStartParam = req.query.lineStart as string;
    const linesParam = req.query.lines as string;

    // Validate parameters
    if (!filePath) {
      throw createError('File path is required', 400, 'missing_path');
    }

    // Prevent path traversal attacks
    const normalizedPath = path.normalize(filePath);
    if (normalizedPath.startsWith('..') || path.isAbsolute(normalizedPath)) {
      throw createError('Invalid file path', 400, 'invalid_path');
    }

    // Check if memory instance is available
    if (!req.memory) {
      throw createError('Memory index not initialized', 503, 'service_unavailable');
    }

    // Parse optional parameters - rag-memory uses 'from' not 'lineStart'
    const options: ReadFileOptions = {};
    if (lineStartParam) {
      const lineStart = parseInt(lineStartParam, 10);
      if (isNaN(lineStart) || lineStart < 1) {
        throw createError('Invalid lineStart parameter', 400, 'invalid_line_start');
      }
      options.from = lineStart;
    }

    if (linesParam) {
      const lines = parseInt(linesParam, 10);
      if (isNaN(lines) || lines < 1) {
        throw createError('Invalid lines parameter', 400, 'invalid_lines');
      }
      options.lines = lines;
    }

    // Read file from rag-memory
    const result = await req.memory.readFile(normalizedPath, options);

    const response: ReadFileResponseBody = {
      content: result.text,
      path: result.path,
    };

    res.json(response);
  })
);

export default router;
