/**
 * Logger Middleware
 * 请求日志中间件
 */

import { Request, Response, NextFunction } from 'express';

/**
 * Request logger middleware
 */
export function logger(req: Request, res: Response, next: NextFunction): void {
  const start = Date.now();

  // Log request
  console.log('[Request]', {
    method: req.method,
    path: req.path,
    query: req.query,
    ip: req.ip,
  });

  // Log response
  res.on('finish', () => {
    const duration = Date.now() - start;
    console.log('[Response]', {
      method: req.method,
      path: req.path,
      status: res.statusCode,
      duration: `${duration}ms`,
    });
  });

  next();
}

// Export as requestLogger for clarity in server.ts
export { logger as requestLogger };
