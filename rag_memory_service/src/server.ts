/**
 * rag-memory HTTP Service
 * Express Server Entry Point
 * rag-memory HTTP 服务入口
 *
 * RAG Memory Integration - Task 1.3
 * Creates Express server wrapper for rag-memory npm package
 */

import express from 'express';
import cors from 'cors';
import type { MemoryIndex } from 'rag-memory';
import { createMemoryIndex } from 'rag-memory';

// Import configuration
import { getConfig, validateConfig, type ServiceConfig } from './config/index.js';

// Import middleware
import { errorHandler, asyncHandler } from './middleware/errorHandler.js';
import { requestLogger } from './middleware/logger.js';

// Import routes
import searchRouter from './routes/search.js';
import statusRouter from './routes/status.js';
import syncRouter from './routes/sync.js';
import readfileRouter from './routes/readfile.js';

// Extend Express Request type
declare global {
  namespace Express {
    interface Request {
      memory?: MemoryIndex;
    }
  }
}

/**
 * Simple logger for server lifecycle events
 */
const log = {
  info: (msg: string, ...args: any[]) => console.log(`[rag-memory:info] ${msg}`, ...args),
  warn: (msg: string, ...args: any[]) => console.warn(`[rag-memory:warn] ${msg}`, ...args),
  error: (msg: string, ...args: any[]) => console.error(`[rag-memory:error] ${msg}`, ...args),
};

/**
 * Create Express application
 */
function createApp(): express.Express {
  const app = express();

  // Middleware
  app.use(cors());
  app.use(express.json());
  app.use(express.urlencoded({ extended: true }));
  app.use(requestLogger);

  return app;
}

/**
 * Load and validate configuration
 * Exit with code 1 if configuration is invalid (Requirement 1.1)
 */
function loadConfig(): ServiceConfig {
  try {
    const config = getConfig();
    validateConfig(config);
    log.info('Configuration loaded successfully');
    log.info(`Documents path: ${config.documentsPath}`);
    log.info(`Index path: ${config.indexPath}`);
    return config;
  } catch (error) {
    if (error instanceof Error) {
      log.error('Configuration error:', error.message);
      process.exit(1);
    }
    throw error;
  }
}

/**
 * Initialize rag-memory index
 * Logs initialization stats (files and chunks) - Requirement 1.1
 */
async function initializeMemory(config: ServiceConfig): Promise<MemoryIndex> {
  log.info('Initializing memory index...');

  try {
    const memory = await createMemoryIndex({
      documentsPath: config.documentsPath,
      indexPath: config.indexPath,
      config: {
        embeddings: {
          provider: 'zhipu',
          model: config.embeddingModel,
          remote: {
            apiKey: config.zhipuApiKey,
          },
        },
        search: {
          maxResults: config.maxSearchResults,
          minScore: 0.0,
          hybrid: {
            enabled: true,
            vectorWeight: config.vectorWeight,
            textWeight: config.bm25Weight,
            candidateMultiplier: 2.0,
          },
        },
        extraPaths: [config.documentsPath], // Add documents path as extra path
      },
      initialSync: true,
    });

    // Log initialization stats - Requirement 1.1
    const status = memory.status();
    log.info('Memory index initialized successfully');
    log.info(`Indexed files: ${status.files}`);
    log.info(`Text chunks: ${status.chunks}`);
    log.info(`Provider: ${status.provider}`);
    log.info(`Model: ${status.model}`);

    return memory;
  } catch (error) {
    log.error('Failed to initialize memory index:', error);
    throw error;
  }
}

/**
 * Register API routes
 */
function registerRoutes(app: express.Express, memory: MemoryIndex): void {
  // Make memory instance available to all routes
  app.use((req, res, next) => {
    req.memory = memory;
    next();
  });

  // Register route modules
  app.use('/api', searchRouter);
  app.use('/api', statusRouter);
  app.use('/api', syncRouter);
  app.use('/api', readfileRouter);

  // Root endpoint
  app.get('/', asyncHandler(async (req, res) => {
    res.json({
      service: 'rag-memory',
      version: '1.0.0',
      status: 'running',
      timestamp: new Date().toISOString(),
    });
  }));

  // 404 handler
  app.use((req, res) => {
    res.status(404).json({
      error: 'not_found',
      message: `Endpoint ${req.method} ${req.path} not found`,
    });
  });

  // Error handler (must be last)
  app.use(errorHandler);
}

/**
 * Start HTTP server
 */
function startServer(app: express.Express, config: ServiceConfig): any {
  const server = app.listen(config.port, config.host, () => {
    log.info(`rag-memory HTTP service started`);
    log.info(`Listening on: http://${config.host}:${config.port}`);
    log.info(`Environment: ${process.env.NODE_ENV || 'development'}`);
    log.info('Ready to accept requests');
  });

  // Set server timeout for graceful shutdown
  server.timeout = 10000; // 10 seconds

  return server;
}

/**
 * Graceful shutdown handler
 * Closes memory index on SIGTERM - Requirement 1.1
 */
async function gracefulShutdown(memory: MemoryIndex, signal: string): Promise<void> {
  log.info(`${signal} received, starting graceful shutdown...`);

  try {
    // Close memory index - Requirement 1.1
    log.info('Closing memory index...');
    await memory.close();
    log.info('Memory index closed');

    log.info('Shutdown complete');
    process.exit(0);
  } catch (error) {
    log.error('Error during shutdown:', error);
    process.exit(1);
  }
}

/**
 * Main server startup
 */
async function main(): Promise<void> {
  // Load configuration (exits with code 1 if invalid) - Requirement 1.1
  const config = loadConfig();

  // Create Express app
  const app = createApp();

  // Initialize memory index
  const memory = await initializeMemory(config);

  // Register routes
  registerRoutes(app, memory);

  // Start server
  startServer(app, config);

  // Setup graceful shutdown handlers - Requirement 1.1
  process.on('SIGTERM', () => gracefulShutdown(memory, 'SIGTERM'));
  process.on('SIGINT', () => gracefulShutdown(memory, 'SIGINT'));

  // Handle uncaught errors
  process.on('uncaughtException', (error: Error) => {
    log.error('Uncaught exception:', error);
    gracefulShutdown(memory, 'UNCAUGHT_EXCEPTION').catch(() => process.exit(1));
  });

  process.on('unhandledRejection', (reason: unknown) => {
    log.error('Unhandled rejection:', reason);
    gracefulShutdown(memory, 'UNHANDLED_REJECTION').catch(() => process.exit(1));
  });
}

// Start server
main().catch((error) => {
  log.error('Failed to start server:', error);
  process.exit(1);
});

// Export for testing
export { createApp, loadConfig, initializeMemory, registerRoutes };
