/**
 * Path utility functions
 */

import fs from 'node:fs/promises';
import path from 'node:path';

/**
 * Normalize a relative path
 */
export function normalizeRelPath(value: string): string {
  const trimmed = value.trim().replace(/^[./]+/, '');
  return trimmed.replace(/\\/g, '/');
}

/**
 * Normalize extra memory paths
 */
export function normalizeExtraPaths(
  workspaceDir: string,
  extraPaths?: string[]
): string[] {
  if (!extraPaths?.length) {
    return [];
  }
  const resolved = extraPaths
    .map((value) => value.trim())
    .filter(Boolean)
    .map((value) =>
      path.isAbsolute(value) ? path.resolve(value) : path.resolve(workspaceDir, value)
    );
  return Array.from(new Set(resolved));
}

/**
 * Check if a path is a memory path
 */
export function isMemoryPath(relPath: string): boolean {
  const normalized = normalizeRelPath(relPath);
  if (!normalized) {
    return false;
  }
  if (normalized === 'MEMORY.md' || normalized === 'memory.md') {
    return true;
  }
  return normalized.startsWith('memory/');
}

/**
 * Walk directory recursively
 */
async function walkDir(dir: string, files: string[]): Promise<void> {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) {
      continue;
    }
    if (entry.isDirectory()) {
      await walkDir(full, files);
      continue;
    }
    if (!entry.isFile()) {
      continue;
    }
    if (!entry.name.endsWith('.md')) {
      continue;
    }
    files.push(full);
  }
}

/**
 * Build a file entry for indexing
 */
export async function buildFileEntry(
  file: string,
  workspaceDir: string
): Promise<{ path: string; absPath: string; mtimeMs: number; size: number; hash: string }> {
  const stat = await fs.stat(file);
  const content = await fs.readFile(file, 'utf-8');
  const hash = Buffer.from(content).toString('base64').slice(0, 16);

  const relPath = path.relative(workspaceDir, file).replace(/\\/g, '/');

  return {
    path: relPath,
    absPath: file,
    mtimeMs: stat.mtimeMs,
    size: stat.size,
    hash,
  };
}

/**
 * List all memory files
 */
export async function listMemoryFiles(
  workspaceDir: string,
  extraPaths?: string[]
): Promise<string[]> {
  const result: string[] = [];
  const memoryFile = path.join(workspaceDir, 'MEMORY.md');
  const altMemoryFile = path.join(workspaceDir, 'memory.md');
  const memoryDir = path.join(workspaceDir, 'memory');

  const addMarkdownFile = async (absPath: string) => {
    try {
      const stat = await fs.lstat(absPath);
      if (stat.isSymbolicLink() || !stat.isFile()) {
        return;
      }
      if (!absPath.endsWith('.md')) {
        return;
      }
      result.push(absPath);
    } catch {}
  };

  await addMarkdownFile(memoryFile);
  await addMarkdownFile(altMemoryFile);
  try {
    const dirStat = await fs.lstat(memoryDir);
    if (!dirStat.isSymbolicLink() && dirStat.isDirectory()) {
      await walkDir(memoryDir, result);
    }
  } catch {}

  const normalizedExtraPaths = normalizeExtraPaths(workspaceDir, extraPaths);
  if (normalizedExtraPaths.length > 0) {
    for (const inputPath of normalizedExtraPaths) {
      try {
        const stat = await fs.lstat(inputPath);
        if (stat.isSymbolicLink()) {
          continue;
        }
        if (stat.isDirectory()) {
          await walkDir(inputPath, result);
          continue;
        }
        if (stat.isFile() && inputPath.endsWith('.md')) {
          result.push(inputPath);
        }
      } catch {}
    }
  }

  if (result.length <= 1) {
    return result;
  }

  // Deduplicate by realpath
  const seen = new Set<string>();
  const deduped: string[] = [];
  for (const entry of result) {
    let key = entry;
    try {
      key = await fs.realpath(entry);
    } catch {}
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    deduped.push(entry);
  }
  return deduped;
}
