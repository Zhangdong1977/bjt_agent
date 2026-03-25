/**
 * Core memory index manager
 */

import path from 'node:path';
import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import Database from 'better-sqlite3';
import chokidar from 'chokidar';
import type {
  MemoryConfig,
  MemoryIndex,
  MemorySearchResult,
  SearchOptions,
  SyncOptions,
  SyncProgress,
  IndexStatus,
  ReadFileOptions,
  EmbeddingProvider,
} from './types.js';
import { ensureMemoryIndexSchema, parseEmbedding } from '../storage/schema.js';
import { listMemoryFiles, buildFileEntry } from '../utils/path.js';
import { chunkMarkdown } from '../utils/chunk.js';
import { hashText } from '../utils/hash.js';
import { buildFtsQuery, bm25RankToScore, mergeHybridResults } from '../search/hybrid.js';

const VECTOR_TABLE = 'chunks_vec';
const FTS_TABLE = 'chunks_fts';
const SNIPPET_MAX_CHARS = 700;

/**
 * Simple logger
 */
const log = {
  debug: (msg: string, ...args: any[]) => console.debug('[rag-memory:debug]', msg, ...args),
  info: (msg: string, ...args: any[]) => console.info('[rag-memory:info]', msg, ...args),
  warn: (msg: string, ...args: any[]) => console.warn('[rag-memory:warn]', msg, ...args),
  error: (msg: string, ...args: any[]) => console.error('[rag-memory:error]', msg, ...args),
};

/**
 * Core memory index manager implementation
 */
export class MemoryIndexManager implements MemoryIndex {
  private config: MemoryConfig;
  private db: Database.Database;
  private provider: EmbeddingProvider;
  private watcher?: chokidar.FSWatcher;
  private watchTimer?: NodeJS.Timeout;
  private dirty = false;
  private closed = false;

  constructor(config: MemoryConfig, provider: EmbeddingProvider) {
    this.config = config;
    this.provider = provider;
    this.db = this.openDatabase();
    this.ensureSchema();
    this.ensureWatcher();
    this.dirty = true;
  }

  /**
   * Search indexed memories
   */
  async search(query: string, options?: SearchOptions): Promise<MemorySearchResult[]> {
    if (this.closed) {
      throw new Error('Index is closed');
    }

    const cleaned = query.trim();
    if (!cleaned) {
      return [];
    }

    // Sync if dirty and configured to do so
    if (this.config.sync.onSearch && this.dirty) {
      await this.sync();
    }

    const minScore = options?.minScore ?? this.config.search.minScore;
    const maxResults = options?.maxResults ?? this.config.search.maxResults;
    const hybrid = this.config.search.hybrid;

    const candidates = Math.min(
      200,
      Math.max(1, Math.floor(maxResults * hybrid.candidateMultiplier))
    );

    // Vector search
    const queryVec = await this.provider.embedQuery(cleaned);
    const vectorResults = await this.searchVector(queryVec, candidates);

    // Keyword search (if enabled)
    let keywordResults: any[] = [];
    if (hybrid.enabled) {
      keywordResults = await this.searchKeyword(cleaned, candidates);
    }

    // Merge or return vector-only results
    if (!hybrid.enabled) {
      return vectorResults
        .filter((entry) => entry.score >= minScore)
        .slice(0, maxResults);
    }

    const merged = mergeHybridResults({
      vector: vectorResults,
      keyword: keywordResults,
      vectorWeight: hybrid.vectorWeight,
      textWeight: hybrid.textWeight,
    });

    return merged
      .filter((entry) => entry.score >= minScore)
      .slice(0, maxResults) as MemorySearchResult[];
  }

  /**
   * Synchronize index with files
   */
  async sync(options?: SyncOptions): Promise<void> {
    if (this.closed) {
      throw new Error('Index is closed');
    }

    const progress = options?.progress
      ? this.createProgressTracker(options.progress)
      : undefined;

    try {
      const files = await listMemoryFiles(this.config.storage.workspaceDir, this.config.extraPaths);
      const fileEntries = await Promise.all(
        files.map(async (file) => buildFileEntry(file, this.config.storage.workspaceDir))
      );

      log.debug('Indexing files', { count: fileEntries.length });

      if (progress) {
        progress.report({ completed: 0, total: fileEntries.length, label: 'Indexing files...' });
      }

      let completed = 0;
      for (const entry of fileEntries) {
        // Check if file needs reindexing
        const existing = this.db
          .prepare('SELECT hash FROM files WHERE path = ?')
          .get(entry.path) as { hash: string } | undefined;

        if (!options?.force && existing?.hash === entry.hash) {
          completed++;
          if (progress) progress.report({ completed, total: fileEntries.length });
          continue;
        }

        await this.indexFile(entry);
        completed++;
        if (progress) progress.report({ completed, total: fileEntries.length });
      }

      // Remove stale files
      const activePaths = new Set(fileEntries.map((e) => e.path));
      const staleRows = this.db.prepare('SELECT path FROM files').all() as Array<{ path: string }>;
      for (const stale of staleRows) {
        if (activePaths.has(stale.path)) continue;
        this.db.prepare('DELETE FROM files WHERE path = ?').run(stale.path);
        this.db.prepare('DELETE FROM chunks WHERE path = ?').run(stale.path);
        try {
          this.db.prepare(`DELETE FROM ${VECTOR_TABLE} WHERE id IN (SELECT id FROM chunks WHERE path = ?)`).run(stale.path);
        } catch {}
        try {
          this.db.prepare(`DELETE FROM ${FTS_TABLE} WHERE path = ?`).run(stale.path);
        } catch {}
      }

      this.dirty = false;
    } catch (err) {
      log.error('Sync failed', err);
      throw err;
    }
  }

  /**
   * Read a file from indexed paths
   */
  async readFile(relPath: string, options?: ReadFileOptions): Promise<{ text: string; path: string }> {
    const rawPath = relPath.trim();
    if (!rawPath) {
      throw new Error('path required');
    }

    const absPath = path.isAbsolute(rawPath)
      ? rawPath
      : path.resolve(this.config.storage.workspaceDir, rawPath);

    const rel = path.relative(this.config.storage.workspaceDir, absPath).replace(/\\/g, '/');

    const stat = await fs.lstat(absPath);
    if (stat.isSymbolicLink() || !stat.isFile()) {
      throw new Error('path not found or not a file');
    }

    const content = await fs.readFile(absPath, 'utf-8');

    if (!options?.from && !options?.lines) {
      return { text: content, path: rel };
    }

    const lines = content.split('\n');
    const start = Math.max(1, options.from ?? 1);
    const count = Math.max(1, options.lines ?? lines.length);
    const slice = lines.slice(start - 1, start - 1 + count);

    return { text: slice.join('\n'), path: rel };
  }

  /**
   * Get index status
   */
  status(): IndexStatus {
    const files = this.db.prepare('SELECT COUNT(*) as c FROM files').get() as { c: number };
    const chunks = this.db.prepare('SELECT COUNT(*) as c FROM chunks').get() as { c: number };

    return {
      files: files.c,
      chunks: chunks.c,
      dirty: this.dirty,
      workspaceDir: this.config.storage.workspaceDir,
      dbPath: this.config.storage.path,
      provider: this.provider.id,
      model: this.provider.model,
      ftsAvailable: this.config.storage.ftsEnabled,
    };
  }

  /**
   * Close the index
   */
  async close(): Promise<void> {
    if (this.closed) {
      return;
    }

    this.closed = true;

    if (this.watchTimer) {
      clearTimeout(this.watchTimer);
      this.watchTimer = undefined;
    }

    if (this.watcher) {
      await this.watcher.close();
      this.watcher = undefined;
    }

    this.db.close();
  }

  // Private methods

  private openDatabase(): Database.Database {
    const dbPath = this.config.storage.path;
    const dir = path.dirname(dbPath);

    try {
      fsSync.mkdirSync(dir, { recursive: true });
    } catch {}

    return new Database(dbPath);
  }

  private ensureSchema(): void {
    const result = ensureMemoryIndexSchema(this.db, this.config.storage.ftsEnabled);
    if (result.ftsError) {
      log.warn('FTS not available', { error: result.ftsError });
    }
  }

  private ensureWatcher(): void {
    if (!this.config.sync.watch || this.watcher) {
      return;
    }

    const watchPaths = [
      path.join(this.config.storage.workspaceDir, 'MEMORY.md'),
      path.join(this.config.storage.workspaceDir, 'memory.md'),
      path.join(this.config.storage.workspaceDir, 'memory'),
      ...(this.config.extraPaths || []),
    ];

    this.watcher = chokidar.watch(watchPaths, {
      ignoreInitial: true,
      awaitWriteFinish: {
        stabilityThreshold: this.config.sync.watchDebounceMs,
        pollInterval: 100,
      },
    });

    const markDirty = () => {
      this.dirty = true;
      this.scheduleWatchSync();
    };

    this.watcher.on('add', markDirty);
    this.watcher.on('change', markDirty);
    this.watcher.on('unlink', markDirty);
  }

  private scheduleWatchSync(): void {
    if (this.watchTimer) {
      clearTimeout(this.watchTimer);
    }

    this.watchTimer = setTimeout(() => {
      this.watchTimer = undefined;
      void this.sync().catch((err) => {
        log.warn('Auto-sync failed', err);
      });
    }, this.config.sync.watchDebounceMs);
  }

  private async searchVector(queryVec: number[], limit: number): Promise<any[]> {
    const results: any[] = [];

    // Simple cosine similarity in JS (for now)
    // TODO: Use sqlite-vec for better performance
    const chunks = this.db
      .prepare('SELECT id, path, start_line, end_line, text, embedding FROM chunks')
      .all() as any[];

    for (const chunk of chunks) {
      const embedding = parseEmbedding(chunk.embedding);
      if (embedding.length === 0) continue;

      const similarity = cosineSimilarity(queryVec, embedding);
      if (similarity > 0) {
        results.push({
          id: chunk.id,
          path: chunk.path,
          startLine: chunk.start_line,
          endLine: chunk.end_line,
          source: 'memory' as const,
          snippet: truncateText(chunk.text, SNIPPET_MAX_CHARS),
          vectorScore: similarity,
        });
      }
    }

    return results
      .sort((a, b) => b.vectorScore - a.vectorScore)
      .slice(0, limit);
  }

  private async searchKeyword(query: string, limit: number): Promise<any[]> {
    if (!this.config.storage.ftsEnabled) {
      return [];
    }

    const ftsQuery = buildFtsQuery(query);
    if (!ftsQuery) {
      return [];
    }

    try {
      const rows = this.db
        .prepare(
          `SELECT id, path, start_line, end_line, snippet(${FTS_TABLE}, 2, '', '', '...', ${SNIPPET_MAX_CHARS}) as snippet
           FROM ${FTS_TABLE}
           WHERE ${FTS_TABLE} MATCH ?
           ORDER BY bm25(${FTS_TABLE}) ASC
           LIMIT ?`
        )
        .all(ftsQuery, limit) as any[];

      return rows.map((row) => ({
        id: row.id,
        path: row.path,
        startLine: row.start_line,
        endLine: row.end_line,
        source: 'memory' as const,
        snippet: row.snippet,
        textScore: bm25RankToScore(0), // Simplified - rank is in ORDER BY
      }));
    } catch (err) {
      log.warn('Keyword search failed', err);
      return [];
    }
  }

  private async indexFile(entry: {
    path: string;
    absPath: string;
    mtimeMs: number;
    size: number;
    hash: string;
  }): Promise<void> {
    const content = await fs.readFile(entry.absPath, 'utf-8');
    const chunks = chunkMarkdown(content, this.config.chunking).filter(
      (chunk) => chunk.text.trim().length > 0
    );

    // Embed chunks
    const embeddings = await this.provider.embedBatch(chunks.map((c) => c.text));

    // Ensure file entry exists in files table (required by foreign key constraint)
    this.db.prepare(
      `INSERT OR REPLACE INTO files (path, source, hash, mtime, size)
       VALUES (?, ?, ?, ?, ?)`
    ).run(entry.path, 'memory', entry.hash, entry.mtimeMs, entry.size);

    // Clear existing data for this file
    this.db.prepare('DELETE FROM chunks WHERE path = ?').run(entry.path);
    try {
      this.db.prepare(`DELETE FROM ${VECTOR_TABLE} WHERE id IN (SELECT id FROM chunks WHERE path = ?)`).run(entry.path);
    } catch {}
    try {
      this.db.prepare(`DELETE FROM ${FTS_TABLE} WHERE path = ?`).run(entry.path);
    } catch {}

    const now = Date.now();

    for (let i = 0; i < chunks.length; i++) {
      const chunk = chunks[i];
      const embedding = embeddings[i] ?? [];
      const id = hashText(`${entry.path}:${chunk.startLine}:${chunk.endLine}:${chunk.hash}`);

      // Insert chunk
      this.db.prepare(
        `INSERT INTO chunks (id, path, source, start_line, end_line, hash, model, text, embedding, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      ).run(
        id,
        entry.path,
        'memory',
        chunk.startLine,
        chunk.endLine,
        chunk.hash,
        this.provider.model,
        chunk.text,
        JSON.stringify(embedding),
        now
      );

      // Insert into vector table (if available)
      if (this.config.storage.vectorEnabled && embedding.length > 0) {
        try {
          this.db.prepare(`INSERT INTO ${VECTOR_TABLE} (id, embedding) VALUES (?, ?)`).run(
            id,
            Buffer.from(new Float32Array(embedding).buffer)
          );
        } catch (err) {
          // sqlite-vec not available, skip
        }
      }

      // Insert into FTS table (if available)
      if (this.config.storage.ftsEnabled) {
        try {
          this.db.prepare(
            `INSERT INTO ${FTS_TABLE} (text, id, path, source, model, start_line, end_line)
             VALUES (?, ?, ?, ?, ?, ?, ?)`
          ).run(chunk.text, id, entry.path, 'memory', this.provider.model, chunk.startLine, chunk.endLine);
        } catch (err) {
          // FTS not available, skip
        }
      }
    }

    // Update file entry
    this.db.prepare(
      `INSERT INTO files (path, source, hash, mtime, size) VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(path) DO UPDATE SET source=excluded.source, hash=excluded.hash, mtime=excluded.mtime, size=excluded.size`
    ).run(entry.path, 'memory', entry.hash, entry.mtimeMs, entry.size);
  }

  private createProgressTracker(
    onProgress: (update: SyncProgress) => void
  ): { report: (update: SyncProgress) => void } {
    let currentLabel: string | undefined;

    return {
      report: (update: SyncProgress) => {
        if (update.label) {
          currentLabel = update.label;
        }
        const label = update.total > 0 && currentLabel
          ? `${currentLabel} ${update.completed}/${update.total}`
          : currentLabel;
        onProgress({ completed: update.completed, total: update.total, label });
      },
    };
  }
}

/**
 * Calculate cosine similarity between two vectors
 */
function cosineSimilarity(a: number[], b: number[]): number {
  if (a.length !== b.length) return 0;

  let dotProduct = 0;
  let normA = 0;
  let normB = 0;

  for (let i = 0; i < a.length; i++) {
    dotProduct += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }

  const denominator = Math.sqrt(normA) * Math.sqrt(normB);
  if (denominator === 0) return 0;

  return dotProduct / denominator;
}

/**
 * Truncate text to max characters
 */
function truncateText(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  return text.slice(0, maxChars) + '...';
}
