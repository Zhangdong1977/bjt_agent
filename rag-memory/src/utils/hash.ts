/**
 * Hash utility functions
 */

import crypto from 'node:crypto';

/**
 * Generate SHA256 hash of text
 */
export function hashText(value: string): string {
  return crypto.createHash('sha256').update(value).digest('hex');
}
