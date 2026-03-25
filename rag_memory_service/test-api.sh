#!/bin/bash
# rag-memory HTTP API 测试脚本

# 配置
API_URL="http://localhost:3001"

echo "========================================"
echo "rag-memory HTTP API 测试"
echo "========================================"
echo ""

# 1. 根端点测试
echo "1. 测试根端点 (GET /)"
curl -s "$API_URL/" | jq '.'
echo ""
echo ""

# 2. 健康检查
echo "2. 测试健康检查 (GET /api/health)"
curl -s "$API_URL/api/health" | jq '.'
echo ""
echo ""

# 3. 获取状态
echo "3. 测试状态查询 (GET /api/status)"
curl -s "$API_URL/api/status" | jq '.'
echo ""
echo ""

# 4. 搜索测试 - 中文查询
echo "4. 测试搜索 (POST /api/search)"
echo "   查询: '安全帽使用规范'"
curl -s -X POST "$API_URL/api/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"安全帽使用规范","limit":5}' | jq '.'
echo ""
echo ""

# 5. 搜索测试 - 关键词
echo "5. 测试搜索关键词 (POST /api/search)"
echo "   查询: '安全带'"
curl -s -X POST "$API_URL/api/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"安全带","limit":3}' | jq '.'
echo ""
echo ""

# 6. 搜索测试 - 防护眼镜
echo "6. 测试搜索防护眼镜 (POST /api/search)"
echo "   查询: '防护眼镜'"
curl -s -X POST "$API_URL/api/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"防护眼镜","limit":3}' | jq '.'
echo ""
echo ""

# 7. 搜索测试 - 高空作业
echo "7. 测试搜索高空作业 (POST /api/search)"
echo "   查询: '高空作业'"
curl -s -X POST "$API_URL/api/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"高空作业","limit":3}' | jq '.'
echo ""
echo ""

# 8. 搜索测试 - 消防安全
echo "8. 测试搜索消防安全 (POST /api/search)"
echo "   查询: '消防安全'"
curl -s -X POST "$API_URL/api/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"消防安全","limit":3}' | jq '.'
echo ""
echo ""

# 9. 搜索测试 - 用电安全
echo "9. 测试搜索用电安全 (POST /api/search)"
echo "   查询: '用电安全'"
curl -s -X POST "$API_URL/api/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"用电安全","limit":3}' | jq '.'
echo ""
echo ""

# 10. 读取文件测试
echo "10. 测试读取文件 (GET /api/readfile?path=ppe.md)"
curl -s "$API_URL/api/readfile?path=ppe.md&lineStart=1&lines=10" | jq '.'
echo ""
echo ""

# 11. 再次获取状态
echo "11. 测试同步后状态 (GET /api/status)"
curl -s "$API_URL/api/status" | jq '.'
echo ""
echo ""

echo "========================================"
echo "测试完成"
echo "========================================"
