# API 使用指南

## 概述

本系统提供完整的 RESTful API，支持多种认证方式和数据交互格式。

## 认证方式

### Bearer Token 认证

所有 API 请求都需要在 HTTP Header 中包含认证令牌：

```http
Authorization: Bearer your-api-token-here
```

获取 API Token 的步骤：
1. 登录系统
2. 访问 /api/tokens 页面
3. 点击"生成新令牌"
4. 复制生成的令牌（仅显示一次）

### OAuth 2.0 认证

系统支持 OAuth 2.0 授权码模式：

1. 注册应用并获取 client_id 和 client_secret
2. 重定向用户到授权端点：
   ```
   https://api.example.com/oauth/authorize?
     client_id=YOUR_CLIENT_ID&
     response_type=code&
     redirect_uri=YOUR_REDIRECT_URI&
     scope=read+write
   ```
3. 获取授权码后，交换访问令牌：
   ```bash
   POST /oauth/token
   Content-Type: application/x-www-form-urlencoded

   grant_type=authorization_code&
   code=AUTHORIZATION_CODE&
   redirect_uri=YOUR_REDIRECT_URI&
   client_id=YOUR_CLIENT_ID&
   client_secret=YOUR_CLIENT_SECRET
   ```

## 核心 API 端点

### 用户管理

#### 创建用户
```http
POST /api/users
Content-Type: application/json

{
  "username": "john_doe",
  "email": "john@example.com",
  "password": "secure_password_123",
  "role": "developer"
}
```

#### 获取用户信息
```http
GET /api/users/{user_id}
```

#### 更新用户
```http
PUT /api/users/{user_id}
Content-Type: application/json

{
  "email": "newemail@example.com",
  "role": "admin"
}
```

### 数据查询

#### 基础查询
```http
GET /api/data?limit=10&offset=0&sort=created_at:desc
```

#### 高级搜索
```http
POST /api/data/search
Content-Type: application/json

{
  "query": "特定关键词",
  "filters": {
    "category": "技术",
    "date_range": {
      "start": "2024-01-01",
      "end": "2024-12-31"
    }
  },
  "pagination": {
    "page": 1,
    "per_page": 20
  }
}
```

## 速率限制

API 调用有以下速率限制：
- 免费用户：100次/小时
- 付费用户：1000次/小时
- 企业版：无限制

超出限制时，API 会返回 HTTP 429 状态码：

```json
{
  "error": "rate_limit_exceeded",
  "message": "API rate limit exceeded. Please try again later.",
  "retry_after": 3600
}
```

## 错误处理

所有 API 错误响应遵循统一格式：

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "输入数据验证失败",
    "details": [
      {
        "field": "email",
        "message": "邮箱格式不正确"
      }
    ]
  }
}
```

常见错误码：
- 400: 请求参数错误
- 401: 未授权
- 403: 禁止访问
- 404: 资源不存在
- 429: 速率限制
- 500: 服务器内部错误

## Webhook 配置

系统支持 Webhook 回调，用于实时数据推送：

```http
POST /api/webhooks
Content-Type: application/json

{
  "url": "https://your-server.com/webhook",
  "events": ["user.created", "data.updated"],
  "secret": "webhook_secret_key"
}
```

Webhook 请求会携带签名用于验证：
```
X-Webhook-Signature: sha256=signature_value
X-Webhook-Timestamp: 1234567890
X-Webhook-Event: user.created
```

## 最佳实践

1. **使用 HTTPS**：所有生产环境 API 调用必须使用 HTTPS
2. **令牌安全**：不要在前端代码中硬编码 API Token
3. **错误重试**：遇到 5xx 错误时，使用指数退避策略重试
4. **缓存响应**：对不频繁变化的数据使用客户端缓存
5. **批量操作**：尽可能使用批量端点减少请求次数
