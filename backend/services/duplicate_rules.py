"""Machine-readable rule contracts for technical-bid duplicate checking.

The rule documents remain Markdown so reviewers can edit them comfortably, but
their small JSON front matter makes retrieval deterministic and auditable.  No
YAML parser is required: every value in the front matter is either a JSON
value or a plain scalar string.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class RuleValidationError(ValueError):
    """Raised when a duplicate rule cannot be safely routed or audited."""


_RULE_ID_RE = re.compile(r"^D\d{3}$")
_ITEM_RE = re.compile(r"^###\s+检查项\s*(\d+)\s*[：:]\s*(.+?)\s*$", re.M)
_ALLOWED_CANDIDATE_TYPES = {
    "paragraph",
    "heading",
    "table",
    "table_row",
    "image",
    "image_ocr",
    "caption",
}
_ALLOWED_CHANNELS = {"lexical", "structure", "semantic", "image"}
_ALLOWED_SOURCE_REQUIREMENTS = {
    "tender_optional",
    "tender_required",
    "public_optional",
    "public_required",
}
_REQUIRED_META = {
    "rule_id",
    "version",
    "title",
    "candidate_types",
    "channels",
    "source_requirements",
    "max_candidates",
    "context_candidates",
    "source_candidates",
    "source_context_candidates",
    "min_evidence_strength",
    "search_terms",
}


@dataclass(frozen=True, slots=True)
class DuplicateRuleSpec:
    """Validated routing and prompting contract for one rule document."""

    rule_id: str
    version: str
    title: str
    candidate_types: tuple[str, ...]
    channels: tuple[str, ...]
    source_requirements: tuple[str, ...]
    max_candidates: int
    context_candidates: int
    source_candidates: int
    source_context_candidates: int
    min_evidence_strength: float
    search_terms: tuple[str, ...]
    raw_text: str
    body: str
    check_items: tuple[dict[str, Any], ...]

    @property
    def source_bases(self) -> tuple[str, ...]:
        """Return source types in stable order, without optional/required suffix."""

        result: list[str] = []
        for requirement in self.source_requirements:
            basis = requirement.split("_", 1)[0]
            if basis not in result:
                result.append(basis)
        return tuple(result)

    @property
    def required_source_bases(self) -> tuple[str, ...]:
        return tuple(
            requirement.split("_", 1)[0]
            for requirement in self.source_requirements
            if requirement.endswith("_required")
        )


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        raise RuleValidationError("front matter value cannot be empty")
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        # Plain, unquoted scalars are accepted for ergonomic hand editing, but
        # never interpreted as executable or YAML-specific syntax.
        if (value.startswith("\"") and value.endswith("\"")) or (
            value.startswith("'") and value.endswith("'")
        ):
            return value[1:-1]
        return value


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    normalized = text.lstrip("\ufeff")
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        raise RuleValidationError("rule must start with JSON front matter delimiter")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise RuleValidationError("front matter closing delimiter is missing") from exc

    metadata: dict[str, Any] = {}
    for line_number, line in enumerate(lines[1:end], 2):
        stripped = line.strip()
        if not stripped:
            continue
        if ":" not in stripped:
            raise RuleValidationError(f"front matter line {line_number} has no key/value separator")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise RuleValidationError(f"invalid front matter key: {key}")
        if key in metadata:
            raise RuleValidationError(f"duplicate front matter key: {key}")
        metadata[key] = _parse_scalar(raw_value)

    body = "\n".join(lines[end + 1 :]).strip() + "\n"
    return metadata, body


def extract_check_items(body: str) -> list[dict[str, Any]]:
    """Extract numbered check items while preserving their human title."""

    return [
        {"number": int(number), "id": f"item-{number}", "title": title.strip()}
        for number, title in _ITEM_RE.findall(body)
        if title.strip()
    ]


def _as_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuleValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _as_string_tuple(value: Any, field_name: str, *, minimum: int = 1) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) < minimum:
        raise RuleValidationError(f"{field_name} must be a list with at least {minimum} item(s)")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise RuleValidationError(f"{field_name} entries must be non-empty strings")
        item = item.strip()
        if item not in values:
            values.append(item)
    return tuple(values)


def _as_int(value: Any, field_name: str, *, minimum: int, maximum: int) -> int:
    # bool is an int subclass but is never a meaningful budget.
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RuleValidationError(f"{field_name} must be an integer in [{minimum}, {maximum}]")
    return value


def _as_float(value: Any, field_name: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleValidationError(f"{field_name} must be numeric")
    value = float(value)
    if not minimum <= value <= maximum:
        raise RuleValidationError(f"{field_name} must be in [{minimum}, {maximum}]")
    return value


def _validate_items(items: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    if len(items) < 5:
        raise RuleValidationError("a rule must define at least five numbered check items")
    numbers = [item["number"] for item in items]
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        raise RuleValidationError(
            f"check item numbers must be contiguous starting at 1, got {numbers}"
        )
    return tuple(items)


def parse_duplicate_rule(text: str, *, source_name: str = "<rule>") -> DuplicateRuleSpec:
    metadata, body = _split_front_matter(text)
    missing = sorted(_REQUIRED_META - metadata.keys())
    if missing:
        raise RuleValidationError(f"{source_name} missing metadata: {', '.join(missing)}")
    unknown = sorted(set(metadata) - _REQUIRED_META)
    if unknown:
        raise RuleValidationError(f"{source_name} has unsupported metadata: {', '.join(unknown)}")

    rule_id = _as_string(metadata["rule_id"], "rule_id")
    if not _RULE_ID_RE.fullmatch(rule_id):
        raise RuleValidationError(f"invalid rule_id: {rule_id}")
    version = _as_string(metadata["version"], "version")
    title = _as_string(metadata["title"], "title")
    candidate_types = _as_string_tuple(metadata["candidate_types"], "candidate_types")
    channels = _as_string_tuple(metadata["channels"], "channels")
    # An empty source list is meaningful for purely bidder-authored structural
    # checks, so allow [] even though the generic helper defaults to one item.
    source_requirements = _as_string_tuple(
        metadata["source_requirements"], "source_requirements", minimum=0
    )

    invalid_types = set(candidate_types) - _ALLOWED_CANDIDATE_TYPES
    if invalid_types:
        raise RuleValidationError(f"unsupported candidate_types: {sorted(invalid_types)}")
    invalid_channels = set(channels) - _ALLOWED_CHANNELS
    if invalid_channels:
        raise RuleValidationError(f"unsupported channels: {sorted(invalid_channels)}")
    invalid_sources = set(source_requirements) - _ALLOWED_SOURCE_REQUIREMENTS
    if invalid_sources:
        raise RuleValidationError(f"unsupported source_requirements: {sorted(invalid_sources)}")

    max_candidates = _as_int(metadata["max_candidates"], "max_candidates", minimum=8, maximum=50)
    context_candidates = _as_int(
        metadata["context_candidates"],
        "context_candidates",
        minimum=0,
        maximum=max_candidates,
    )
    source_candidates = _as_int(
        metadata["source_candidates"], "source_candidates", minimum=0, maximum=30
    )
    source_context_candidates = _as_int(
        metadata["source_context_candidates"],
        "source_context_candidates",
        minimum=0,
        maximum=source_candidates,
    )
    min_evidence_strength = _as_float(
        metadata["min_evidence_strength"],
        "min_evidence_strength",
        minimum=0.0,
        maximum=1.0,
    )
    search_terms = _as_string_tuple(metadata["search_terms"], "search_terms", minimum=4)
    if any(len(term) < 2 for term in search_terms):
        raise RuleValidationError("search_terms entries must contain at least two characters")

    first_heading = next((line.strip() for line in body.splitlines() if line.strip()), "")
    expected_heading = f"# {rule_id} {title}"
    if first_heading != expected_heading:
        raise RuleValidationError(
            f"{source_name} first heading must be exactly '{expected_heading}'"
        )
    items = _validate_items(extract_check_items(body))
    return DuplicateRuleSpec(
        rule_id=rule_id,
        version=version,
        title=title,
        candidate_types=tuple(candidate_types),
        channels=tuple(channels),
        source_requirements=source_requirements,
        max_candidates=max_candidates,
        context_candidates=context_candidates,
        source_candidates=source_candidates,
        source_context_candidates=source_context_candidates,
        min_evidence_strength=min_evidence_strength,
        search_terms=search_terms,
        raw_text=text,
        body=body,
        check_items=items,
    )


def load_duplicate_rule(path: str | Path) -> DuplicateRuleSpec:
    path = Path(path)
    if not path.is_file():
        raise RuleValidationError(f"rule file does not exist: {path}")
    try:
        rule = parse_duplicate_rule(path.read_text(encoding="utf-8"), source_name=path.name)
        expected_name = f"{rule.rule_id} {rule.title}.md"
        if path.name != expected_name:
            raise RuleValidationError(
                f"{path.name} must match rule id/title filename '{expected_name}'"
            )
        return rule
    except UnicodeDecodeError as exc:
        raise RuleValidationError(f"rule file is not UTF-8: {path.name}") from exc


def load_duplicate_rules(directory: str | Path) -> list[DuplicateRuleSpec]:
    directory = Path(directory)
    if not directory.is_dir():
        raise RuleValidationError(f"rule directory does not exist: {directory}")
    rules = [load_duplicate_rule(path) for path in sorted(directory.glob("*.md"))]
    if not rules:
        raise RuleValidationError("rule directory contains no Markdown rules")
    ids = [rule.rule_id for rule in rules]
    if len(ids) != len(set(ids)):
        raise RuleValidationError("duplicate rule_id in rule directory")
    return sorted(rules, key=lambda rule: rule.rule_id)


def build_rule_query(rule: DuplicateRuleSpec) -> str:
    """Build a compact query from declared domain terms, not prose instructions."""

    pieces: list[str] = []
    for value in (rule.title, *rule.search_terms):
        value = value.strip()
        if value and value not in pieces:
            pieces.append(value)
    query = " ".join(pieces)
    return query[:800]


def _payload_types(payload: dict[str, Any]) -> set[str]:
    types: set[str] = set()
    for key in ("left_location", "right_location"):
        location = payload.get(key) or {}
        if isinstance(location, dict) and location.get("content_type"):
            types.add(str(location["content_type"]))
    if payload.get("content_type"):
        types.add(str(payload["content_type"]))
    return types


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def candidate_channels(payload: dict[str, Any]) -> set[str]:
    """Infer the deterministic evidence channels present in a candidate."""

    channels: set[str] = set()
    score_map = {
        "lexical": payload.get("lexical_score", 0.0),
        "structure": payload.get("structure_score", 0.0),
        "semantic": payload.get("semantic_score", 0.0),
        "image": payload.get("image_score", 0.0),
    }
    for channel, value in score_map.items():
        if _float_or_zero(value) > 0.05:
            channels.add(channel)
    table = payload.get("table_comparison") or {}
    if isinstance(table, dict) and any(
        _float_or_zero(table.get(key)) > 0.05
        for key in (
            "header_similarity",
            "row_alignment_score",
            "numeric_signature_score",
            "rare_cell_overlap",
            "table_structure_score",
        )
    ):
        channels.add("structure")
    image_types = _payload_types(payload) & {"image", "image_ocr"}
    if image_types and payload.get("image_comparison"):
        channels.add("image")
    if not channels:
        match_type = str(payload.get("match_type") or "")
        fallback = {
            "exact": "lexical",
            "near_exact": "lexical",
            "structural": "structure",
            "semantic": "semantic",
            "ocr_error": "image",
            "logic_anomaly": "structure",
        }.get(match_type)
        if fallback:
            channels.add(fallback)
    return channels


def filter_candidate_payloads(
    rule: DuplicateRuleSpec,
    candidates: Iterable[dict[str, Any]],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Apply rule-level type/channel/evidence gates to immutable tool payloads."""

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        content_types = _payload_types(candidate)
        if content_types and not content_types <= set(rule.candidate_types):
            continue
        if not content_types:
            # An untyped payload cannot be safely routed to a type-specific rule.
            continue
        evidence_strength = _float_or_zero(candidate.get("evidence_strength"))
        image_score = _float_or_zero(candidate.get("image_score"))
        # Image blocks often have no textual payload, so the generic evidence
        # score (which intentionally rewards long, unique text) can be near
        # zero even for an exact binary hash.  Preserve that deterministic
        # evidence without weakening the threshold for ordinary boilerplate.
        image_comparison = candidate.get("image_comparison") or {}
        exact_image = bool(
            isinstance(image_comparison, dict)
            and image_comparison.get("left_image_sha256")
            and image_comparison.get("left_image_sha256")
            == image_comparison.get("right_image_sha256")
        )
        table_comparison = candidate.get("table_comparison") or {}
        structured_signal = 0.0
        if isinstance(table_comparison, dict):
            structured_signal = max(
                _float_or_zero(table_comparison.get("numeric_signature_score")),
                _float_or_zero(table_comparison.get("rare_cell_overlap")),
                _float_or_zero(table_comparison.get("row_alignment_score")),
            )
        content_types = _payload_types(candidate)
        ocr_signal = (
            0.35
            if str(candidate.get("match_type") or "") == "ocr_error"
            and "image_ocr" in content_types
            else 0.0
        )
        routing_strength = max(
            evidence_strength,
            0.75 if exact_image else (0.45 * image_score if image_score >= 0.78 else 0.0),
            0.35 if structured_signal >= 0.75 else 0.0,
            ocr_signal,
        )
        if routing_strength < rule.min_evidence_strength:
            continue
        if not (candidate_channels(candidate) & set(rule.channels)):
            continue
        selected.append(candidate)
        seen.add(candidate_id)

    selected.sort(
        key=lambda item: (
            _float_or_zero(item.get("rank_score", item.get("similarity_score", 0.0))),
            _float_or_zero(item.get("evidence_strength")),
            _float_or_zero(item.get("similarity_score")),
        ),
        reverse=True,
    )
    cap = rule.max_candidates if limit is None else max(0, min(int(limit), rule.max_candidates))
    return selected[:cap]


__all__ = [
    "DuplicateRuleSpec",
    "RuleValidationError",
    "build_rule_query",
    "candidate_channels",
    "extract_check_items",
    "filter_candidate_payloads",
    "load_duplicate_rule",
    "load_duplicate_rules",
    "parse_duplicate_rule",
]
