"""Load, validate, and snapshot a duplicate-check Markdown rule file."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class StructureRule(BaseModel):
    min_real_headings: int = Field(3, ge=1, le=100)
    require_multiple_levels: bool = False


class DuplicateRuleConfig(BaseModel):
    version: str = "1"
    chunk_size_chars: int = Field(1200, ge=200, le=5000)
    chunk_overlap_chars: int = Field(150, ge=0, le=1000)
    lexical_threshold: float = Field(0.78, ge=0, le=1)
    semantic_threshold: float = Field(0.82, ge=0, le=1)
    top_k_per_chunk: int = Field(5, ge=1, le=20)
    max_candidates_per_pair: int = Field(100, ge=1, le=500)
    min_evidence_chars: int = Field(50, ge=10, le=1000)
    structure: StructureRule = Field(default_factory=StructureRule)
    exclude_section_title_patterns: list[str] = Field(default_factory=list)
    exclude_text_patterns: list[str] = Field(default_factory=list)

    @field_validator("exclude_section_title_patterns", "exclude_text_patterns")
    @classmethod
    def validate_patterns(cls, values: list[str]) -> list[str]:
        if len(values) > 50:
            raise ValueError("排除正则最多 50 条")
        for pattern in values:
            if len(pattern) > 300:
                raise ValueError("单条排除正则过长")
            re.compile(pattern)
        return values


class DuplicateRule(BaseModel):
    source_path: str
    name: str
    sha256: str
    config: DuplicateRuleConfig
    instructions: str
    snapshot_path: str | None = None


def load_duplicate_rule(path: str | Path) -> DuplicateRule:
    rule_path = Path(path).resolve()
    if not rule_path.is_file():
        raise FileNotFoundError(f"查重规则文件不存在：{rule_path}")
    content = rule_path.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError("查重规则文件为空")

    metadata: dict = {}
    instructions = content
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end < 0:
            raise ValueError("查重规则 YAML Front Matter 未闭合")
        metadata = yaml.safe_load(content[3:end]) or {}
        instructions = content[end + 4 :].strip()

    return DuplicateRule(
        source_path=str(rule_path),
        name=rule_path.name,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        config=DuplicateRuleConfig.model_validate(metadata),
        instructions=instructions,
    )


def snapshot_duplicate_rule(rule: DuplicateRule, target_dir: str | Path) -> DuplicateRule:
    directory = Path(target_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{Path(rule.name).stem}_{rule.sha256[:12]}.md"
    shutil.copy2(rule.source_path, target)
    return rule.model_copy(update={"snapshot_path": str(target)})
