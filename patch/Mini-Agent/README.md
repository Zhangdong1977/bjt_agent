# Mini-Agent Patch

## 说明

本目录存放对 Mini-Agent 子模块的本地修改补丁。

## 应用补丁

当同事同步代码后，需要在子模块中应用补丁：

```bash
cd Mini-Agent
git am --signoff < patch/Mini-Agent/xxxx.patch
```

## 当前补丁

| 文件 | 说明 | 日期 |
|------|------|------|
| Mini-Agent_timeout_tokens.patch | 为 LLM 客户端添加 timeout 和 max_tokens 支持 | 2026-04-17 |
