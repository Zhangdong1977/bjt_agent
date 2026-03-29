// Type declarations for rag-memory
declare module 'rag-memory' {
  export interface IndexStatus {
    files: number;
    chunks: number;
    dirty: boolean;
    workspaceDir: string;
    dbPath: string;
    provider: string;
    model: string;
    cacheEntries?: number;
    vectorAvailable?: boolean;
    ftsAvailable?: boolean;
  }

  export interface SearchOptions {
    maxResults?: number;
    minScore?: number;
  }

  export interface SyncOptions {
    force?: boolean;
  }

  export interface ReadFileOptions {
    from?: number;
    lines?: number;
  }

  export interface MemoryIndex {
    search(query: string, options?: SearchOptions): Promise<any[]>;
    sync(options?: SyncOptions): Promise<void>;
    status(): IndexStatus;
    readFile(path: string, options?: ReadFileOptions): Promise<{ text: string; path: string }>;
    close(): Promise<void>;
  }

  export interface CreateMemoryIndexOptions {
    documentsPath?: string;
    indexPath?: string;
    config?: any;
    initialSync?: boolean;
  }

  export function createMemoryIndex(options?: CreateMemoryIndexOptions): Promise<MemoryIndex>;
}
