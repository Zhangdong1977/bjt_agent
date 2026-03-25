/**
 * Service Configuration
 * 服务配置管理
 *
 * RAG Memory Service Configuration Module
 * rag-memory 服务配置管理模块
 */

import path from 'node:path';
import dotenv from 'dotenv';

// Load environment variables
dotenv.config();

export interface ServiceConfig {
  // Server configuration / 服务器配置
  port: number;
  host: string;

  // Paths / 路径配置
  documentsPath: string;
  indexPath: string;

  // ZhipuAI configuration / 智谱AI配置
  zhipuApiKey: string;
  embeddingModel: string;  // 模型名称

  // Service configuration / 服务配置
  logLevel: 'debug' | 'info' | 'warn' | 'error';
  maxSearchResults: number;
  searchTimeout: number;

  // Search configuration / 搜索配置
  vectorWeight: number;    // 向量搜索权重 (0-1)
  bm25Weight: number;      // BM25 搜索权重 (0-1)

  // Cache configuration / 缓存配置
  enableCache: boolean;
  cacheTtl: number;        // 缓存过期时间（秒）

  // Health check configuration / 健康检查配置
  healthCheckInterval: number;  // 健康检查间隔（秒）
  healthCheckTimeout: number;   // 健康检查超时（秒）
}

/**
 * Get configuration from environment variables with defaults
 * 从环境变量获取配置，使用默认值
 */
export function getConfig(): ServiceConfig {
  const port = parseInt(process.env.PORT || '3001', 10);
  const host = process.env.HOST || '0.0.0.0';
  const documentsPath = process.env.DOCUMENTS_PATH || path.join(process.cwd(), 'knowledge_docs');
  const indexPath = process.env.INDEX_PATH || path.join(process.cwd(), 'data', 'memory.sqlite');
  const zhipuApiKey = process.env.ZHIPU_API_KEY || '';
  const embeddingModel = process.env.EMBEDDING_MODEL || 'embedding-3';
  const logLevel = (process.env.LOG_LEVEL as ServiceConfig['logLevel']) || 'info';
  const maxSearchResults = parseInt(process.env.MAX_SEARCH_RESULTS || '50', 10);
  const searchTimeout = parseInt(process.env.SEARCH_TIMEOUT || '5000', 10);

  // Search weights / 搜索权重
  const vectorWeight = parseFloat(process.env.VECTOR_WEIGHT || '0.7');
  const bm25Weight = parseFloat(process.env.BM25_WEIGHT || '0.3');

  // Cache settings / 缓存设置
  const enableCache = process.env.ENABLE_CACHE !== 'false';  // 默认启用
  const cacheTtl = parseInt(process.env.CACHE_TTL || '300', 10);  // 默认 5 分钟

  // Health check settings / 健康检查设置
  const healthCheckInterval = parseInt(process.env.HEALTH_CHECK_INTERVAL || '30', 10);
  const healthCheckTimeout = parseInt(process.env.HEALTH_CHECK_TIMEOUT || '10', 10);

  // Validate required configuration / 验证必需配置
  if (!zhipuApiKey) {
    throw new Error('ZHIPU_API_KEY environment variable is required');
  }

  return {
    port,
    host,
    documentsPath: path.resolve(documentsPath),
    indexPath: path.resolve(indexPath),
    zhipuApiKey,
    embeddingModel,
    logLevel,
    maxSearchResults,
    searchTimeout,
    vectorWeight,
    bm25Weight,
    enableCache,
    cacheTtl,
    healthCheckInterval,
    healthCheckTimeout,
  };
}

/**
 * Validate configuration
 * 验证配置
 */
export function validateConfig(config: ServiceConfig): void {
  // Server port validation / 端口验证
  if (config.port < 1 || config.port > 65535) {
    throw new Error(`Invalid port: ${config.port}. Must be between 1 and 65535.`);
  }

  // Max search results validation / 最大结果数验证
  if (config.maxSearchResults < 1 || config.maxSearchResults > 100) {
    throw new Error(`Invalid maxSearchResults: ${config.maxSearchResults}. Must be between 1 and 100.`);
  }

  // Search timeout validation / 搜索超时验证
  if (config.searchTimeout < 100 || config.searchTimeout > 30000) {
    throw new Error(`Invalid searchTimeout: ${config.searchTimeout}. Must be between 100 and 30000.`);
  }

  // Search weights validation / 搜索权重验证
  if (config.vectorWeight < 0 || config.vectorWeight > 1) {
    throw new Error(`Invalid vectorWeight: ${config.vectorWeight}. Must be between 0 and 1.`);
  }

  if (config.bm25Weight < 0 || config.bm25Weight > 1) {
    throw new Error(`Invalid bm25Weight: ${config.bm25Weight}. Must be between 0 and 1.`);
  }

  if (Math.abs(config.vectorWeight + config.bm25Weight - 1.0) > 0.01) {
    throw new Error(
      `Search weights must sum to 1.0. Got vectorWeight=${config.vectorWeight}, bm25Weight=${config.bm25Weight}`
    );
  }

  // Cache TTL validation / 缓存 TTL 验证
  if (config.cacheTtl < 0 || config.cacheTtl > 3600) {
    throw new Error(`Invalid cacheTtl: ${config.cacheTtl}. Must be between 0 and 3600.`);
  }

  // Health check validation / 健康检查验证
  if (config.healthCheckInterval < 1 || config.healthCheckInterval > 300) {
    throw new Error(`Invalid healthCheckInterval: ${config.healthCheckInterval}. Must be between 1 and 300.`);
  }

  if (config.healthCheckTimeout < 1 || config.healthCheckTimeout > 60) {
    throw new Error(`Invalid healthCheckTimeout: ${config.healthCheckTimeout}. Must be between 1 and 60.`);
  }
}
