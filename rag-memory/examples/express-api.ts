/**
 * Express API example for rag-memory
 *
 * This creates a simple REST API for searching your knowledge base
 *
 * Run with: tsx examples/express-api.ts
 */

import express, { Request, Response } from 'express';
import { createMemoryIndex } from '../src/index.js';

const app = express();
app.use(express.json());

let memory: Awaited<ReturnType<typeof createMemoryIndex>>;

// Initialize memory index
async function initMemory() {
  memory = await createMemoryIndex({
    documentsPath: './docs',
    config: {
      embeddings: {
        provider: 'openai',
        remote: {
          apiKey: process.env.OPENAI_API_KEY || '',
        },
      },
    },
  });
  console.log('✅ Memory index initialized');
}

/**
 * GET /api/status
 * Get index status
 */
app.get('/api/status', async (_req: Request, res: Response) => {
  try {
    const status = memory.status();
    res.json({
      ok: true,
      status,
    });
  } catch (error) {
    res.status(500).json({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
});

/**
 * GET /api/search
 * Search the memory index
 *
 * Query params:
 * - q: search query
 * - maxResults: maximum number of results (optional)
 * - minScore: minimum score threshold (optional)
 */
app.get('/api/search', async (req: Request, res: Response) => {
  try {
    const { q, maxResults, minScore } = req.query;

    if (!q || typeof q !== 'string') {
      return res.status(400).json({
        ok: false,
        error: 'Query parameter "q" is required',
      });
    }

    const results = await memory.search(q, {
      maxResults: maxResults ? parseInt(maxResults as string) : undefined,
      minScore: minScore ? parseFloat(minScore as string) : undefined,
    });

    res.json({
      ok: true,
      query: q,
      count: results.length,
      results,
    });
  } catch (error) {
    res.status(500).json({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
});

/**
 * POST /api/sync
 * Manually trigger index synchronization
 */
app.post('/api/sync', async (req: Request, res: Response) => {
  try {
    await memory.sync({
      progress: (update) => {
        console.log(`Sync progress: ${update.completed}/${update.total}`);
      },
    });

    res.json({
      ok: true,
      message: 'Sync completed',
    });
  } catch (error) {
    res.status(500).json({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
});

/**
 * GET /api/file/:path
 * Read a specific file from the index
 */
app.get('/api/file/:path(*)', async (req: Request, res: Response) => {
  try {
    const { path } = req.params;
    const { from, lines } = req.query;

    const result = await memory.readFile(path, {
      from: from ? parseInt(from as string) : undefined,
      lines: lines ? parseInt(lines as string) : undefined,
    });

    res.json({
      ok: true,
      path: result.path,
      text: result.text,
    });
  } catch (error) {
    res.status(404).json({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
});

// Start server
const PORT = process.env.PORT || 3000;

initMemory().then(() => {
  app.listen(PORT, () => {
    console.log(`🚀 Server running at http://localhost:${PORT}`);
    console.log(`\nEndpoints:`);
    console.log(`  GET  /api/status   - Get index status`);
    console.log(`  GET  /api/search   - Search documents`);
    console.log(`  POST /api/sync     - Sync index`);
    console.log(`  GET  /api/file/*   - Read a file`);
  });
});

// Graceful shutdown
process.on('SIGINT', async () => {
  console.log('\n\n🛑 Shutting down...');
  await memory.close();
  process.exit(0);
});
