/**
 * Text chunking utilities
 */

import type { MemoryChunk } from '../core/types.js';

/**
 * Chunk markdown content into smaller pieces
 */
export function chunkMarkdown(
  content: string,
  chunking: { tokens: number; overlap: number }
): MemoryChunk[] {
  const maxChars = Math.max(32, chunking.tokens * 4);
  const overlapChars = Math.max(0, chunking.overlap * 4);

  const lines = content.split('\n');
  const chunks: MemoryChunk[] = [];

  let currentChunk: string[] = [];
  let currentLength = 0;
  let startLine = 1;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const lineLength = line.length + 1; // +1 for newline

    if (currentLength + lineLength > maxChars && currentChunk.length > 0) {
      // Save current chunk
      const chunkText = currentChunk.join('\n');
      chunks.push({
        startLine,
        endLine: i,
        text: chunkText,
        hash: hashText(chunkText),
      });

      // Start new chunk with overlap
      const overlapLines = Math.floor(overlapChars / 80); // Rough estimate
      currentChunk = currentChunk.slice(-overlapLines);
      currentLength = currentChunk.reduce((sum, l) => sum + l.length + 1, 0);
      startLine = i - overlapLines + 1;
    }

    currentChunk.push(line);
    currentLength += lineLength;
  }

  // Don't forget the last chunk
  if (currentChunk.length > 0) {
    const chunkText = currentChunk.join('\n');
    chunks.push({
      startLine,
      endLine: lines.length,
      text: chunkText,
      hash: hashText(chunkText),
    });
  }

  return chunks;
}

/**
 * Simple hash function for chunking
 */
function hashText(text: string): string {
  // Use a simple hash for now - can be replaced with SHA256 if needed
  let hash = 0;
  for (let i = 0; i < text.length; i++) {
    const char = text.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash; // Convert to 32-bit integer
  }
  return hash.toString(16);
}
