/**
 * Sync Route
 * 同步路由
 *
 * RAG Memory Integration - Task 1.5
 * Implements manual sync with progress tracking and statistics
 */

import { Router, Request, Response } from 'express';
import { asyncHandler, createError } from '../middleware/errorHandler.js';
import fs from 'node:fs/promises';
import path from 'node:path';
import { parseDocument } from '../parsers/document_parser.js';
import { getConfig } from '../config/index.js';

// Import types from rag-memory
import type { MemoryIndex, IndexStatus, SyncOptions } from 'rag-memory';
import type { IndexManager } from '../indexManager.js';

// Extend Request to include indexManager
declare global {
  namespace Express {
    interface Request {
      indexManager?: IndexManager;
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
 * Convert DOCX/PDF files to Markdown before sync
 */
async function convertDocumentsToMarkdown(
  documentsPath: string
): Promise<{ converted: number; errors: string[] }> {
  let converted = 0;
  const errors: string[] = [];

  async function walkDir(dir: string): Promise<void> {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        await walkDir(fullPath);
        continue;
      }
      if (!entry.isFile()) continue;

      const ext = path.extname(entry.name).toLowerCase();
      if (ext === '.docx' || ext === '.pdf') {
        const mdFileName = entry.name + '.md';
        const mdFilePath = path.join(path.dirname(fullPath), mdFileName);

        // Skip if already converted
        try {
          const mdStat = await fs.stat(mdFilePath);
          const origStat = await fs.stat(fullPath);
          if (mdStat.mtimeMs >= origStat.mtimeMs) {
            console.log(`[sync] Skipping ${entry.name} (already converted)`);
            continue;
          }
        } catch {
          // Continue if md file doesn't exist
        }

        try {
          console.log(`[sync] Converting ${entry.name} to Markdown...`);
          const config = getConfig();
          const result = await parseDocument(fullPath, {
            apiKey: config.zhipuApiKey,
          });

          // Write as markdown with original filename in title
          const markdown = `# ${result.fileName}\n\n${result.content}`;
          await fs.writeFile(mdFilePath, markdown, 'utf-8');
          converted++;
          console.log(
            `[sync] Converted ${entry.name} -> ${mdFileName} (${result.content.length} chars)`
          );
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          errors.push(`Failed to convert ${entry.name}: ${msg}`);
          console.error(`[sync] Error converting ${entry.name}: ${msg}`);
        }
      }
    }
  }

  try {
    await walkDir(documentsPath);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    errors.push(`Walk error: ${msg}`);
  }

  return { converted, errors };
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
    const userId = req.headers['x-user-id'] as string;

    if (!userId) {
      throw createError('X-User-ID header is required', 400, 'missing_user_id');
    }

    const { force = false }: SyncRequestBody = req.body;

    const manager = req.indexManager;
    if (!manager) {
      throw createError('Index manager not initialized', 503, 'service_unavailable');
    }

    // Get user's index (creates if not exists)
    const memory = await manager.getIndex(userId);

    // Get status before sync for statistics tracking
    const statusBefore: IndexStatus = memory.status();
    const errors: string[] = [];

    // Convert DOCX/PDF to Markdown before sync
    const config = getConfig();
    const userDocumentsPath = path.join(config.documentsPath, userId);
    console.log(`[sync] Converting documents in ${userDocumentsPath}...`);
    const convertResult = await convertDocumentsToMarkdown(userDocumentsPath);
    if (convertResult.errors.length > 0) {
      errors.push(...convertResult.errors);
    }

    // Perform sync with progress tracking
    const startTime = Date.now();
    try {
      await memory.sync({
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
    const statusAfter: IndexStatus = memory.status();

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
