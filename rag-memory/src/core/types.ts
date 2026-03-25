/**
 * Core type definitions for RAG Memory system
 */

/**
 * Embedding provider types
 */
export type EmbeddingProviderType = 'openai' | 'gemini' | 'zhipu' | 'local' | 'custom';

/**
 * Memory source type
 */
export type MemorySource = 'memory' | 'sessions' | 'knowledge';

/**
 * Search result with metadata
 */
export interface MemorySearchResult {
  /** File path relative to workspace */
  path: string;
  /** Start line number in file */
  startLine: number;
  /** End line number in file */
  endLine: number;
  /** Relevance score (0-1, higher is better) */
  score: number;
  /** Text snippet from the matched section */
  snippet: string;
  /** Source of the memory */
  source: MemorySource;
}

/**
 * File entry for indexing
 */
export interface MemoryFileEntry {
  /** Relative path */
  path: string;
  /** Absolute file path */
  absPath: string;
  /** Last modified time in milliseconds */
  mtimeMs: number;
  /** File size in bytes */
  size: number;
  /** Content hash (SHA256) */
  hash: string;
}

/**
 * Text chunk for embedding
 */
export interface MemoryChunk {
  /** Start line number */
  startLine: number;
  /** End line number */
  endLine: number;
  /** Chunk text content */
  text: string;
  /** Content hash */
  hash: string;
}

/**
 * Embedding provider interface
 */
export interface EmbeddingProvider {
  /** Unique provider identifier */
  id: string;
  /** Model name/identifier */
  model: string;
  /** Optional: embedding dimensions */
  dimensions?: number;

  /**
   * Generate embedding for a single query text
   * @param text - Query text to embed
   * @returns Embedding vector
   */
  embedQuery(text: string): Promise<number[]>;

  /**
   * Generate embeddings for multiple texts in batch
   * @param texts - Array of texts to embed
   * @returns Array of embedding vectors
   */
  embedBatch(texts: string[]): Promise<number[][]>;
}

/**
 * Chunking configuration
 */
export interface ChunkingConfig {
  /** Target chunk size in tokens (default: 400) */
  tokens: number;
  /** Overlap between chunks in tokens (default: 80) */
  overlap: number;
}

/**
 * Storage configuration
 */
export interface StorageConfig {
  /** Path to SQLite database */
  path: string;
  /** Path to documents directory */
  workspaceDir: string;
  /** Enable vector search extension */
  vectorEnabled: boolean;
  /** Optional: custom path to sqlite-vec extension */
  vectorExtensionPath?: string;
  /** Enable full-text search */
  ftsEnabled: boolean;
}

/**
 * Remote embedding service configuration
 */
export interface RemoteEmbeddingConfig {
  /** Base URL for API (optional, uses default if not specified) */
  baseUrl?: string;
  /** API key for authentication */
  apiKey?: string;
  /** Additional HTTP headers */
  headers?: Record<string, string>;
  /** Embedding dimensions (for providers that support it, like Zhipu AI) */
  dimensions?: number;
  /** Batch processing configuration */
  batch?: {
    /** Enable batch API usage */
    enabled: boolean;
    /** Wait for batch completion */
    wait: boolean;
    /** Number of concurrent batch jobs */
    concurrency: number;
    /** Poll interval in ms */
    pollIntervalMs: number;
    /** Timeout in minutes */
    timeoutMinutes: number;
  };
}

/**
 * Local embedding configuration
 */
export interface LocalEmbeddingConfig {
  /** Path to GGUF model file or HuggingFace URI */
  modelPath?: string;
  /** Directory to cache downloaded models */
  modelCacheDir?: string;
}

/**
 * Embedding configuration
 */
export interface EmbeddingConfig {
  /** Provider type */
  provider: EmbeddingProviderType;
  /** Model name (optional, uses default if not specified) */
  model?: string;
  /** Remote service configuration */
  remote?: RemoteEmbeddingConfig;
  /** Local embedding configuration */
  local?: LocalEmbeddingConfig;
  /** Custom embedding provider instance */
  customProvider?: EmbeddingProvider;
  /** Fallback provider if primary fails */
  fallback?: EmbeddingProviderType;
}

/**
 * Hybrid search configuration
 */
export interface HybridSearchConfig {
  /** Enable hybrid search (vector + keyword) */
  enabled: boolean;
  /** Weight for vector search score (0-1) */
  vectorWeight: number;
  /** Weight for keyword search score (0-1) */
  textWeight: number;
  /** Multiplier for candidate pool size */
  candidateMultiplier: number;
}

/**
 * Search configuration
 */
export interface SearchConfig {
  /** Maximum number of results to return */
  maxResults: number;
  /** Minimum relevance score threshold (0-1) */
  minScore: number;
  /** Hybrid search settings */
  hybrid: HybridSearchConfig;
}

/**
 * Synchronization configuration
 */
export interface SyncConfig {
  /** Sync on search if dirty */
  onSearch: boolean;
  /** Watch files for changes */
  watch: boolean;
  /** Debounce delay for file watcher in ms */
  watchDebounceMs: number;
  /** Periodic sync interval in minutes (0 to disable) */
  intervalMinutes: number;
}

/**
 * Cache configuration
 */
export interface CacheConfig {
  /** Enable embedding cache */
  enabled: boolean;
  /** Maximum number of cache entries */
  maxEntries?: number;
}

/**
 * Main memory configuration
 */
export interface MemoryConfig {
  /** Storage settings */
  storage: StorageConfig;
  /** Embedding settings */
  embeddings: EmbeddingConfig;
  /** Text chunking settings */
  chunking: ChunkingConfig;
  /** Search behavior settings */
  search: SearchConfig;
  /** Synchronization settings */
  sync: SyncConfig;
  /** Cache settings */
  cache: CacheConfig;
  /** Additional paths to index */
  extraPaths?: string[];
  /** Enabled memory sources */
  sources?: MemorySource[];
}

/**
 * Search options for individual queries
 */
export interface SearchOptions {
  /** Maximum results (overrides config) */
  maxResults?: number;
  /** Minimum score (overrides config) */
  minScore?: number;
}

/**
 * Synchronization progress update
 */
export interface SyncProgress {
  /** Number of completed items */
  completed: number;
  /** Total number of items */
  total: number;
  /** Optional label/status message */
  label?: string;
}

/**
 * Synchronization options
 */
export interface SyncOptions {
  /** Force full reindex */
  force?: boolean;
  /** Progress callback */
  progress?: (update: SyncProgress) => void;
}

/**
 * File reading options
 */
export interface ReadFileOptions {
  /** Start line (1-based, inclusive) */
  from?: number;
  /** Number of lines to read */
  lines?: number;
}

/**
 * Index status information
 */
export interface IndexStatus {
  /** Number of indexed files */
  files: number;
  /** Number of indexed chunks */
  chunks: number;
  /** Whether index needs sync */
  dirty: boolean;
  /** Current workspace directory */
  workspaceDir: string;
  /** Database path */
  dbPath: string;
  /** Embedding provider ID */
  provider: string;
  /** Embedding model name */
  model: string;
  /** Number of cached embeddings */
  cacheEntries?: number;
  /** Vector extension availability */
  vectorAvailable?: boolean;
  /** FTS availability */
  ftsAvailable?: boolean;
}

/**
 * Main memory index interface
 */
export interface MemoryIndex {
  /**
   * Search indexed memories
   * @param query - Search query text
   * @param options - Optional search parameters
   * @returns Array of search results sorted by relevance
   */
  search(query: string, options?: SearchOptions): Promise<MemorySearchResult[]>;

  /**
   * Synchronize index with files
   * @param options - Optional sync parameters
   */
  sync(options?: SyncOptions): Promise<void>;

  /**
   * Read a file from the indexed paths
   * @param path - Relative file path
   * @param options - Optional read parameters
   * @returns File content with path
   */
  readFile(path: string, options?: ReadFileOptions): Promise<{ text: string; path: string }>;

  /**
   * Get current index status
   * @returns Index status information
   */
  status(): IndexStatus;

  /**
   * Close the index and release resources
   */
  close(): Promise<void>;
}
