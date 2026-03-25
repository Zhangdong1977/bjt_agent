/**
 * SQLite database schema for RAG Memory index
 */

import type { Database } from 'better-sqlite3';

const VECTOR_TABLE = 'chunks_vec';
const FTS_TABLE = 'chunks_fts';
const EMBEDDING_CACHE_TABLE = 'embedding_cache';

export interface SchemaResult {
  ftsAvailable: boolean;
  ftsError?: string;
}

/**
 * Ensure database schema is initialized
 * @param db - Database instance
 * @param ftsEnabled - Whether to enable full-text search
 * @returns Schema creation result
 */
export function ensureMemoryIndexSchema(
  db: Database,
  ftsEnabled: boolean
): SchemaResult {
  // Create metadata table
  db.exec(`
    CREATE TABLE IF NOT EXISTS meta (
      key TEXT PRIMARY KEY,
      value TEXT
    )
  `);

  // Create files table
  db.exec(`
    CREATE TABLE IF NOT EXISTS files (
      path TEXT PRIMARY KEY,
      source TEXT NOT NULL,
      hash TEXT NOT NULL,
      mtime INTEGER NOT NULL,
      size INTEGER NOT NULL
    )
  `);

  // Create chunks table
  db.exec(`
    CREATE TABLE IF NOT EXISTS chunks (
      id TEXT PRIMARY KEY,
      path TEXT NOT NULL,
      source TEXT NOT NULL,
      start_line INTEGER NOT NULL,
      end_line INTEGER NOT NULL,
      hash TEXT NOT NULL,
      model TEXT NOT NULL,
      text TEXT NOT NULL,
      embedding TEXT,
      updated_at INTEGER NOT NULL,
      FOREIGN KEY (path) REFERENCES files(path) ON DELETE CASCADE
    )
  `);

  // Create indexes for faster lookups
  db.exec(`
    CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
    CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);
    CREATE INDEX IF NOT EXISTS idx_chunks_model ON chunks(model);
  `);

  // Create embedding cache table
  db.exec(`
    CREATE TABLE IF NOT EXISTS ${EMBEDDING_CACHE_TABLE} (
      provider TEXT NOT NULL,
      model TEXT NOT NULL,
      provider_key TEXT NOT NULL,
      hash TEXT NOT NULL,
      embedding TEXT NOT NULL,
      dims INTEGER NOT NULL,
      updated_at INTEGER NOT NULL,
      PRIMARY KEY (provider, model, provider_key, hash)
    )
  `);

  // Create index for cache pruning
  db.exec(`
    CREATE INDEX IF NOT EXISTS idx_embedding_cache_updated
    ON ${EMBEDDING_CACHE_TABLE}(updated_at)
  `);

  // Create full-text search table
  let ftsAvailable = false;
  let ftsError: string | undefined;

  if (ftsEnabled) {
    try {
      db.exec(`
        CREATE VIRTUAL TABLE IF NOT EXISTS ${FTS_TABLE} USING fts5(
          text,
          id,
          path,
          source,
          model,
          start_line,
          end_line
        )
      `);
      ftsAvailable = true;
    } catch (err) {
      ftsError = err instanceof Error ? err.message : String(err);
      // FTS5 not available, continue without it
    }
  }

  return { ftsAvailable, ftsError };
}

/**
 * Get vector table name
 */
export function getVectorTableName(): string {
  return VECTOR_TABLE;
}

/**
 * Get FTS table name
 */
export function getFtsTableName(): string {
  return FTS_TABLE;
}

/**
 * Get embedding cache table name
 */
export function getEmbeddingCacheTableName(): string {
  return EMBEDDING_CACHE_TABLE;
}

/**
 * Parse embedding from JSON string
 */
export function parseEmbedding(json: string): number[] {
  try {
    const parsed = JSON.parse(json);
    if (Array.isArray(parsed)) {
      return parsed;
    }
  } catch {}
  return [];
}

/**
 * Convert embedding array to JSON string
 */
export function stringifyEmbedding(embedding: number[]): string {
  return JSON.stringify(embedding);
}

/**
 * Convert embedding array to binary buffer for sqlite-vec
 */
export function embeddingToBuffer(embedding: number[]): Buffer {
  return Buffer.from(new Float32Array(embedding).buffer);
}
