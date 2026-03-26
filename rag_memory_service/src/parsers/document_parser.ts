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
 */
async function parseDocxFile(filePath: string): Promise<{ content: string }> {
  try {
    const mammoth = await import('mammoth');
    const result = await mammoth.extractRawText({ path: filePath });
    return {
      content: result.value,
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'MODULE_NOT_FOUND') {
      throw new Error('mammoth package not installed. Run: npm install mammoth');
    }
    throw error;
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

  const data = await response.json();
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
