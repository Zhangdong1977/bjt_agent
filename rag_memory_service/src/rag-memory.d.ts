// Type declarations for rag-memory
declare module 'rag-memory' {
  export interface MemoryIndex {
    search(query: string, options?: any): Promise<any[]>;
    sync(options?: any): Promise<any>;
    status(): any;
    readFile(path: string, options?: any): Promise<any>;
    close(): Promise<void>;
  }

  export function createMemoryIndex(options?: any): Promise<MemoryIndex>;

  export interface IndexStatus {
    files: number;
    chunks: number;
    dirty: boolean;
    provider: string;
    model: string;
  }

  export interface SyncOptions {
    force?: boolean;
  }

  export interface ReadFileOptions {
    from?: number;
    lines?: number;
  }
}
