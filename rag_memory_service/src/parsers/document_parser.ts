/**
 * Document Parser
 * 文档解析器 - 支持 PDF, DOCX, 图片等格式
 */

import fs from 'node:fs';
import path from 'node:path';

export interface ParsedDocument {
  content: string;
  fileType: string;
  fileName: string;
  pageCount?: number;
  imageCount?: number;
  metadata?: Record<string, unknown>;
}

export interface DocumentParserConfig {
  apiKey: string;
  apiBase?: string;
}

/**
 * Detect file type from extension
 */
export function detectFileType(filename: string): string {
  const ext = path.extname(filename).toLowerCase().slice(1);
  return ext || 'unknown';
}

/**
 * Check if file type is supported
 */
export function isSupported(fileType: string): boolean {
  const supportedTypes = ['pdf', 'docx', 'doc', 'txt', 'md', 'png', 'jpg', 'jpeg'];
  return supportedTypes.includes(fileType.toLowerCase());
}

/**
 * Parse text-based documents (txt, md)
 */
async function parseTextFile(filePath: string): Promise<string> {
  const content = await fs.promises.readFile(filePath, 'utf-8');
  return content;
}

/**
 * Parse PDF documents
 * Requires pdf-parse package
 */
async function parsePdfFile(filePath: string): Promise<{ content: string; pageCount: number }> {
  try {
    // Dynamic import to handle missing dependency gracefully
    const pdfParse = (await import('pdf-parse')).default;
    const dataBuffer = await fs.promises.readFile(filePath);
    const data = await pdfParse(dataBuffer);
    return {
      content: data.text,
      pageCount: data.numpages,
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'MODULE_NOT_FOUND') {
      throw new Error('pdf-parse package not installed. Run: npm install pdf-parse');
    }
    throw error;
  }
}

/**
 * Parse DOCX documents
 * Requires mammoth package
 *
 * Large file handling (210MB+):
 * - Hard limit at 500MB to prevent memory exhaustion
 * - Timeout scaling: 2min (<50MB) up to 15min (200MB+)
 * - Uses path-based extraction (not buffer) to avoid double memory usage
 * - Detailed error context for debugging
 */
async function parseDocxFile(filePath: string): Promise<{ content: string }> {
  // File size thresholds (in MB)
  const LARGE_FILE_THRESHOLD = 50;  // Start warning at 50MB
  const VERY_LARGE_THRESHOLD = 150; // Enable extended timeout at 150MB+
  const HUGE_THRESHOLD = 200;       // Extra protection at 200MB+
  const MAX_FILE_SIZE = 500;        // Hard reject limit

  // Check file size first
  const stats = await fs.promises.stat(filePath);
  const fileSizeMB = stats.size / (1024 * 1024);

  // Hard reject for files over 500MB
  if (fileSizeMB > MAX_FILE_SIZE) {
    throw new Error(
      `DOCX file too large (${fileSizeMB.toFixed(2)}MB). ` +
      `Maximum supported size is ${MAX_FILE_SIZE}MB. ` +
      `Please split the document into smaller parts.`
    );
  }

  // Calculate appropriate timeout based on file size
  let timeoutMs: number;
  if (fileSizeMB >= HUGE_THRESHOLD) {
    timeoutMs = 15 * 60 * 1000; // 15 minutes for huge files
    console.warn(`[DOCX Parser] Huge file (${fileSizeMB.toFixed(2)}MB) - 15min timeout`);
  } else if (fileSizeMB >= VERY_LARGE_THRESHOLD) {
    timeoutMs = 10 * 60 * 1000; // 10 minutes for very large files
    console.warn(`[DOCX Parser] Very large file (${fileSizeMB.toFixed(2)}MB) - 10min timeout`);
  } else if (fileSizeMB > LARGE_FILE_THRESHOLD) {
    timeoutMs = 5 * 60 * 1000;  // 5 minutes for large files
    console.warn(`[DOCX Parser] Large file (${fileSizeMB.toFixed(2)}MB) - 5min timeout`);
  } else {
    timeoutMs = 2 * 60 * 1000;  // 2 minutes for normal files
  }

  let timeoutHandle: NodeJS.Timeout | null = null;

  try {
    const mammoth = await import('mammoth');

    // Create timeout with cleanup reference
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeoutHandle = setTimeout(() => {
        reject(new Error(`DOCX parsing timeout after ${timeoutMs / 1000}s for ${fileSizeMB.toFixed(2)}MB file`));
      }, timeoutMs);
    });

    // Use path-based extraction (not buffer) to avoid double memory usage
    // Buffer approach: load file (210MB) -> mammoth processes -> total 420MB+ peak
    // Path approach: mammoth streams the file directly -> lower peak memory
    console.log(`[DOCX Parser] Starting extraction for ${fileSizeMB.toFixed(2)}MB file...`);

    const extractionPromise = (async () => {
      // Use path instead of buffer for large files to reduce peak memory
      const result = await mammoth.extractRawText({ path: filePath });

      // Log warnings if any
      if (result.messages && result.messages.length > 0) {
        const warnings = result.messages.filter((m: { type: string }) => m.type === 'warning');
        if (warnings.length > 0) {
          console.warn(`[DOCX Parser] ${warnings.length} warnings during extraction`);
          warnings.forEach((w: { message: string }) => console.warn(`  - ${w.message}`));
        }
      }

      return result;
    })();

    // Race between extraction and timeout
    const result = await Promise.race([extractionPromise, timeoutPromise]);

    console.log(`[DOCX Parser] Extraction complete: ${result.value.length} chars from ${fileSizeMB.toFixed(2)}MB file`);

    return {
      content: result.value,
    };
  } catch (error) {
    // Enhance error message with file context
    const errorMessage = error instanceof Error ? error.message : String(error);

    if ((error as NodeJS.ErrnoException).code === 'MODULE_NOT_FOUND') {
      throw new Error('mammoth package not installed. Run: npm install mammoth');
    }

    if (errorMessage.includes('timeout')) {
      throw new Error(
        `DOCX parsing timeout: ${errorMessage}. ` +
        `File size: ${fileSizeMB.toFixed(2)}MB. ` +
        `Consider splitting the document into smaller parts (<100MB each).`
      );
    }

    // Memory errors often contain specific keywords
    if (
      errorMessage.includes('memory') ||
      errorMessage.includes('heap') ||
      errorMessage.includes('allocation') ||
      errorMessage.includes('ENOMEM')
    ) {
      throw new Error(
        `DOCX parsing memory error for ${fileSizeMB.toFixed(2)}MB file: ${errorMessage}. ` +
        `The file is too large to process in memory. ` +
        `Consider splitting the document into smaller parts (<100MB each) or using a different tool.`
      );
    }

    // For unknown errors, provide helpful context
    throw new Error(`DOCX parsing failed for ${filePath} (${fileSizeMB.toFixed(2)}MB): ${errorMessage}`);
  } finally {
    // Cleanup timeout handle to prevent memory leaks
    if (timeoutHandle) {
      clearTimeout(timeoutHandle);
    }
  }
}

/**
 * Parse image using Mini-Max Vision API
 */
async function parseImageWithVision(
  filePath: string,
  apiKey: string,
  apiBase: string = 'https://api.minimaxi.com'
): Promise<string> {
  const imageBuffer = await fs.promises.readFile(filePath);
  const base64Image = imageBuffer.toString('base64');
  const fileType = detectFileType(filePath);

  // Mini-Max Vision API call
  const response = await fetch(`${apiBase}/v1/images/generations`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: 'MiniMax-VL-01',
      messages: [
        {
          role: 'user',
          content: '请描述这张图片中的文字内容，保持原有格式。',
        },
      ],
      image_base64: base64Image,
    }),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Vision API error: ${response.status} - ${error}`);
  }

  const data = await response.json() as { choices?: { message?: { content?: string } }[] };
  return data.choices?.[0]?.message?.content || 'No text extracted from image';
}

/**
 * Parse document based on file type
 */
export async function parseDocument(
  filePath: string,
  config: DocumentParserConfig
): Promise<ParsedDocument> {
  const fileType = detectFileType(filePath);
  const fileName = path.basename(filePath);

  let content: string;
  let metadata: Record<string, unknown> = {};

  switch (fileType) {
    case 'txt':
    case 'md':
      content = await parseTextFile(filePath);
      break;

    case 'pdf':
      const pdfResult = await parsePdfFile(filePath);
      content = pdfResult.content;
      metadata.pageCount = pdfResult.pageCount;
      break;

    case 'docx':
    case 'doc':
      const docxResult = await parseDocxFile(filePath);
      content = docxResult.content;
      break;

    case 'png':
    case 'jpg':
    case 'jpeg':
      content = await parseImageWithVision(filePath, config.apiKey, config.apiBase);
      metadata.imageCount = 1;
      break;

    default:
      throw new Error(`Unsupported file type: ${fileType}`);
  }

  return {
    content,
    fileType,
    fileName,
    ...metadata,
  };
}

export default { parseDocument, detectFileType, isSupported };
