# backend/tests/test_rule_parser.py
import pytest
from pathlib import Path
import tempfile
import json
from backend.agent.master.tools.rule_parser import RuleParserTool, RuleLibraryScannerTool


@pytest.fixture
def sample_rule_doc():
    content = """# 资质要求

## 投标人资质验证
检查投标人是否具备招标文件要求的资质证书

## 信用评级
检查投标人信用评级是否满足要求
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        temp_path = f.name
    yield temp_path
    Path(temp_path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_rule_parser(sample_rule_doc):
    tool = RuleParserTool()
    result = await tool.execute(sample_rule_doc)
    assert result.success is True
    data = json.loads(result.content)
    assert data["total_count"] == 2
    assert "投标人资质验证" in [item["title"] for item in data["check_items"]]


@pytest.mark.asyncio
async def test_rule_library_scanner():
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "rule_001.md").write_text("# Rule 1")
        Path(tmpdir, "rule_002.md").write_text("# Rule 2")

        tool = RuleLibraryScannerTool()
        result = await tool.execute(tmpdir)
        assert result.success is True
        data = json.loads(result.content)
        assert data["total_count"] == 2