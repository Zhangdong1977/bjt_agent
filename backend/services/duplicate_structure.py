"""Build a trustworthy, persisted section index for duplicate checking."""

from __future__ import annotations

import json
import re
from pathlib import Path

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def build_structure_index(markdown_path: str | Path) -> tuple[Path, dict]:
    path = Path(markdown_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    sections: list[dict] = []
    stack: list[dict] = []

    for line_index, line in enumerate(lines, start=1):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        section = {
            "section_id": f"s{len(sections) + 1}",
            "title": match.group(2).strip(),
            "level": level,
            "parent_id": stack[-1]["section_id"] if stack else None,
            "start_line": line_index,
            "end_line": len(lines),
            "page_start": None,
            "page_end": None,
            "is_virtual": False,
        }
        sections.append(section)
        stack.append(section)

    # A section includes its descendant subsections. It ends only when the next
    # heading at the same or a higher level begins, not at the first child
    # heading. This lets the content tool return a complete chapter subtree.
    for index, section in enumerate(sections):
        next_boundary = next(
            (
                candidate["start_line"] - 1
                for candidate in sections[index + 1 :]
                if candidate["level"] <= section["level"]
            ),
            len(lines),
        )
        section["end_line"] = next_boundary

    real_heading_count = len(sections)
    levels = sorted({s["level"] for s in sections})
    if real_heading_count >= 3:
        quality = "reliable"
    elif real_heading_count:
        quality = "weak"
    else:
        quality = "none"

    analysis = {
        "version": 1,
        "quality": quality,
        "quality_reasons": [
            f"real_heading_count={real_heading_count}",
            f"heading_levels={','.join(map(str, levels)) or 'none'}",
        ],
        "source": "markdown_headings",
        "real_heading_count": real_heading_count,
        "max_level": max(levels) if levels else 0,
        "sections": sections,
    }
    index_path = path.with_name(f"{path.stem.removesuffix('_parsed')}_structure.json")
    index_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    return index_path, analysis


def read_section(markdown_path: str | Path, section: dict) -> str:
    lines = Path(markdown_path).read_text(encoding="utf-8").splitlines()
    start = max(0, int(section["start_line"]) - 1)
    end = min(len(lines), int(section["end_line"]))
    content = lines[start:end]
    if content and _HEADING_RE.match(content[0]):
        content = content[1:]
    return "\n".join(content).strip()
