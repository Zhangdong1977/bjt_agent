/**
 * TDD Tests for Embedding API
 * RED: These tests define expected behavior
 */

import { describe, it, expect, beforeAll, afterAll } from 'vitest';

// Test configuration
const TEST_API_KEY = 'f2b0a46b0022430da56b57fb729730b8.fuqqiPR9pK86tysK';
const TEST_TEXT = '这是一个测试文本';
const API_URL = 'https://open.bigmodel.cn/api/paas/v4/embeddings';

describe('Embedding API Integration', () => {
  describe('ZhipuAI Embedding Service', () => {
    it('should generate embedding for valid text', async () => {
      const response = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${TEST_API_KEY}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: 'embedding-3',
          input: TEST_TEXT,
        }),
      });

      expect(response.ok).toBe(true);

      const data = await response.json();
      expect(data.data).toBeDefined();
      expect(data.data.length).toBeGreaterThan(0);
      expect(data.data[0].embedding).toBeDefined();
      expect(Array.isArray(data.data[0].embedding)).toBe(true);
      expect(data.data[0].embedding.length).toBeGreaterThan(0); // Should be 1024 dimensions
    });

    it('should reject invalid API key', async () => {
      const response = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Authorization': 'Bearer invalid_key_test',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: 'embedding-3',
          input: TEST_TEXT,
        }),
      });

      // Should return 401 or similar error
      expect(response.ok).toBe(false);
      const data = await response.json();
      expect(data.error).toBeDefined();
    });

    it('should handle empty input gracefully', async () => {
      const response = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${TEST_API_KEY}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: 'embedding-3',
          input: '',
        }),
      });

      // API should return error for empty input
      expect(response.ok).toBe(false);
    });

    it('should return consistent embeddings for same text', async () => {
      const payload = {
        model: 'embedding-3',
        input: TEST_TEXT,
      };

      const response1 = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${TEST_API_KEY}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      const response2 = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${TEST_API_KEY}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      const data1 = await response1.json();
      const data2 = await response2.json();

      // Embeddings should be identical for same input
      expect(data1.data[0].embedding).toEqual(data2.data[0].embedding);
    });

    it('should generate different embeddings for different texts', async () => {
      const response1 = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${TEST_API_KEY}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: 'embedding-3',
          input: '苹果',
        }),
      });

      const response2 = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${TEST_API_KEY}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: 'embedding-3',
          input: '香蕉',
        }),
      });

      const data1 = await response1.json();
      const data2 = await response2.json();

      // Different texts should have different embeddings
      expect(data1.data[0].embedding).not.toEqual(data2.data[0].embedding);
    });
  });
});
