/**
 * File utility functions
 */

import fsSync from 'node:fs';

/**
 * Ensure directory exists
 */
export function ensureDir(dir: string): string {
  try {
    fsSync.mkdirSync(dir, { recursive: true });
  } catch {}
  return dir;
}
