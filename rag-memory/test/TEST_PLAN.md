# RAG Memory 完整测试计划

## 测试范围

### 1. 基础功能测试
- [x] 包构建和安装
- [x] 配置系统
- [x] 文件索引
- [x] 文本分块
- [x] 数据库存储

### 2. Embedding Provider 测试
- [ ] Mock Provider（本地测试）
- [ ] Zhipu AI（智谱AI）
- [ ] OpenAI（如果有 API key）
- [ ] Gemini（如果有 API key）
- [ ] 自定义 Provider

### 3. 搜索功能测试
- [ ] 中文搜索
- [ ] 英文搜索
- [ ] 混合语言搜索
- [ ] 语义搜索
- [ ] 关键词搜索
- [ ] 混合搜索（vector + BM25）

### 4. 边缘情况测试
- [ ] 空查询
- [ ] 无效路径
- [ ] 空文档
- [ ] 特殊字符
- [ ] 超长文本
- [ ] 并发操作

### 5. 性能测试
- [ ] 索引速度
- [ ] 搜索速度
- [ ] 内存占用
- [ ] 大文件处理

### 6. API 测试
- [ ] createMemoryIndex
- [ ] search
- [ ] sync
- [ ] readFile
- [ ] status
- [ ] close

---

## 测试执行顺序

1. **Phase 1**: 基础功能（Mock）
2. **Phase 2**: 智谱AI 真实测试
3. **Phase 3**: 边缘情况
4. **Phase 4**: 性能测试
5. **Phase 5**: 报告生成
