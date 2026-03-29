#!/bin/bash
# Knowledge Base Cleanup Script
# 清理所有用户的知识库文档和索引
#
# 使用方法: ./cleanup_knowledge.sh [--force]
#   --force: 跳过确认提示，直接执行清理

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Paths
WORKSPACE_KNOWLEDGE="workspace/knowledge"
RAG_KNOWLEDGE_DOCS="rag_memory_service/knowledge_docs"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"
}

error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1"
}

echo_step() {
    echo -e "${BLUE}[$1]${NC} $2"
}

# Parse arguments
FORCE_MODE=false
if [[ "$1" == "--force" ]]; then
    FORCE_MODE=true
fi

# Check if paths exist and collect stats
echo ""
log "========================================"
log "  Knowledge Base Cleanup Script"
log "========================================"
echo ""

# Stop services first
echo_step "1/4" "Stopping services..."
if ./scripts/bjt.sh status 2>/dev/null | grep -q "Running"; then
    warn "Services are running. Stopping them first..."
    ./scripts/bjt.sh stop 2>/dev/null || true
    sleep 2
fi
log "Services stopped"

# Collect statistics
echo ""
echo_step "2/4" "Collecting statistics..."
echo ""

if [ -d "$WORKSPACE_KNOWLEDGE" ]; then
    WORKSPACE_USERS=$(find "$WORKSPACE_KNOWLEDGE" -maxdepth 1 -type d | wc -l)
    WORKSPACE_FILES=$(find "$WORKSPACE_KNOWLEDGE" -type f 2>/dev/null | wc -l)
    WORKSPACE_SIZE=$(du -sh "$WORKSPACE_KNOWLEDGE" 2>/dev/null | cut -f1 || echo "0")
    echo "  Backend Knowledge Base:"
    echo "    - Users: $((WORKSPACE_USERS - 1))"
    echo "    - Files: $WORKSPACE_FILES"
    echo "    - Size: $WORKSPACE_SIZE"
else
    echo "  Backend Knowledge Base: 不存在或为空"
fi

echo ""

if [ -d "$RAG_KNOWLEDGE_DOCS" ]; then
    RAG_USERS=$(find "$RAG_KNOWLEDGE_DOCS" -maxdepth 1 -type d | wc -l)
    RAG_FILES=$(find "$RAG_KNOWLEDGE_DOCS" -type f 2>/dev/null | wc -l)
    RAG_SIZE=$(du -sh "$RAG_KNOWLEDGE_DOCS" 2>/dev/null | cut -f1 || echo "0")
    echo "  RAG Memory Service:"
    echo "    - Users: $((RAG_USERS - 1))"
    echo "    - Files: $RAG_FILES"
    echo "    - Size: $RAG_SIZE"
else
    echo "  RAG Memory Service: 不存在或为空"
fi

echo ""

# Confirmation prompt
if [ "$FORCE_MODE" != true ]; then
    echo -e "${RED}警告：此操作将删除以下内容：${NC}"
    echo "  1. 所有用户的上传文档"
    echo "  2. 所有用户的 RAG 索引"
    echo "  3. 所有转换后的 Markdown 文件"
    echo ""
    echo -e "${RED}此操作不可逆！${NC}"
    echo ""
    read -p "确认执行清理? (yes/no): " -r
    if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        log "操作已取消"
        exit 0
    fi
fi

# Perform cleanup
echo ""
echo_step "3/4" "Deleting knowledge base files..."
echo ""

# Delete workspace knowledge base
if [ -d "$WORKSPACE_KNOWLEDGE" ]; then
    USER_COUNT=$(find "$WORKSPACE_KNOWLEDGE" -maxdepth 1 -mindepth 1 -type d | wc -l)
    if [ "$USER_COUNT" -gt 0 ]; then
        log "Deleting workspace/knowledge/* ($USER_COUNT users)..."
        rm -rf "$WORKSPACE_KNOWLEDGE"/*
        log "Workspace knowledge base cleaned"
    else
        log "Workspace knowledge base is already empty"
    fi
else
    log "Workspace knowledge base does not exist"
fi

# Delete RAG knowledge docs
if [ -d "$RAG_KNOWLEDGE_DOCS" ]; then
    RAG_USER_COUNT=$(find "$RAG_KNOWLEDGE_DOCS" -maxdepth 1 -mindepth 1 -type d | wc -l)
    if [ "$RAG_USER_COUNT" -gt 0 ]; then
        log "Deleting rag_memory_service/knowledge_docs/* ($RAG_USER_COUNT users)..."
        rm -rf "$RAG_KNOWLEDGE_DOCS"/*
        log "RAG knowledge docs cleaned"
    else
        log "RAG knowledge docs is already empty"
    fi
else
    log "RAG knowledge docs directory does not exist"
fi

# Recreate empty directories to maintain structure
mkdir -p "$WORKSPACE_KNOWLEDGE"
mkdir -p "$RAG_KNOWLEDGE_DOCS"

echo ""
echo_step "4/4" "Verification..."
echo ""

# Verify cleanup
if [ -z "$(ls -A "$WORKSPACE_KNOWLEDGE" 2>/dev/null)" ]; then
    echo -e "  Workspace knowledge: ${GREEN}已清空${NC}"
else
    echo -e "  Workspace knowledge: ${RED}警告 - 仍有文件残留${NC}"
fi

if [ -z "$(ls -A "$RAG_KNOWLEDGE_DOCS" 2>/dev/null)" ]; then
    echo -e "  RAG knowledge docs: ${GREEN}已清空${NC}"
else
    echo -e "  RAG knowledge docs: ${RED}警告 - 仍有文件残留${NC}"
fi

echo ""
log "========================================"
log "  Knowledge Base Cleanup Completed"
log "========================================"
echo ""
log "所有用户文档和索引已清除"
echo ""
