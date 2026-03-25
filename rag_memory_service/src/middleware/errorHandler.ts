/**
 * Error Handler Middleware
 * 错误处理中间件
 */

import { Request, Response, NextFunction } from 'express';

export interface ApiError extends Error {
  statusCode?: number;
  code?: string;
  details?: unknown;
}

/**
 * Format error response
 */
function formatError(error: Error | ApiError) {
  const isApiError = 'statusCode' in error;

  return {
    error: isApiError && (error as ApiError).code ? (error as ApiError).code : 'internal_error',
    message: error.message || 'An unexpected error occurred',
    details: isApiError && (error as ApiError).details ? (error as ApiError).details : undefined,
  };
}

/**
 * Error handler middleware
 */
export function errorHandler(
  error: Error | ApiError,
  req: Request,
  res: Response,
  next: NextFunction
): void {
  const statusCode = (error as ApiError).statusCode || 500;
  const formatted = formatError(error);

  // Log error
  console.error('[Error]', {
    method: req.method,
    path: req.path,
    statusCode,
    error: formatted,
    stack: error.stack,
  });

  res.status(statusCode).json(formatted);
}

/**
 * Create API error
 */
export function createError(
  message: string,
  statusCode: number = 500,
  code?: string,
  details?: unknown
): ApiError {
  const error = new Error(message) as ApiError;
  error.statusCode = statusCode;
  if (code) error.code = code;
  if (details) error.details = details;
  return error;
}

/**
 * Async handler wrapper to catch errors in async route handlers
 */
export function asyncHandler(
  fn: (req: Request, res: Response, next: NextFunction) => Promise<void>
) {
  return (req: Request, res: Response, next: NextFunction) => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
}
