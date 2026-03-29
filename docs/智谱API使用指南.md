# 智谱 API 使用指南

## 配置位置

`rag_memory_service/.env`

```
ZHIPU_API_KEY=your-api-key
EMBEDDING_MODEL=embedding-3
```

## Embedding 接口

### 默认维度（1536维）

```bash
curl -X POST \
https://open.bigmodel.cn/api/paas/v4/embeddings \
-H "Authorization: Bearer your-api-key" \
-H "Content-Type: application/json" \
-d '{
    "model": "embedding-3",
    "input": "这是一段需要向量化的文本"
}'
```

### 自定义维度

```bash
curl -X POST \
https://open.bigmodel.cn/api/paas/v4/embeddings \
-H "Authorization: Bearer your-api-key" \
-H "Content-Type: application/json" \
-d '{
    "model": "embedding-3",
    "input": "这是一段需要向量化的文本",
    "dimensions": 512
}'
```

## 返回格式

```json
{
  "data": [{
    "embedding": [0.0027873022, ...],
    "index": 0,
    "object": "embedding"
  }],
  "model": "embedding-3",
  "object": "list",
  "usage": {
    "completion_tokens": 0,
    "prompt_tokens": 11,
    "total_tokens": 11
  }
}
```

## 注意事项

- `input` 字段支持单条文本或数组
- `dimensions` 可选，默认 1536
- 计费按 `total_tokens` 计算
