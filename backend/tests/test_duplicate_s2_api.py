"""S2-3/S2-4 API boundary and migration-contract tests.

These tests intentionally use lightweight database doubles.  They exercise the
authorization and validation branches without requiring a running PostgreSQL,
Celery worker, or external billing provider.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api import documents as documents_api
from backend.api import duplicate_check as duplicate_api
from backend.schemas.document import (
    DuplicateBatchAttachRequest,
    DuplicateBatchMemberAttach,
)
from backend.schemas.duplicate_check import (
    DuplicateClusterResponse,
    DuplicateCoverageResponse,
    DuplicateMatrixResponse,
    DuplicateOccurrenceResponse,
)


class _ScalarRows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, *, one=None, rows=(), scalar_rows=None):
        self._one = one
        self._rows = list(rows)
        self._scalar_rows = list(scalar_rows if scalar_rows is not None else rows)

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return _ScalarRows(self._scalar_rows)

    def all(self):
        return list(self._rows)


class _SequenceDB:
    def __init__(self, *results):
        self.results = list(results)
        self.committed = False

    async def execute(self, _query):
        assert self.results, "unexpected database execute"
        return self.results.pop(0)

    def add(self, _value):
        return None

    async def commit(self):
        self.committed = True

    async def flush(self):
        return None

    async def rollback(self):
        return None

    async def refresh(self, _value):
        return None


def _project(*, mode="batch", project_id="project-1", user_id="user-1"):
    return SimpleNamespace(
        id=project_id,
        user_id=user_id,
        project_type="duplicate",
        duplicate_mode=mode,
        is_deleted=False,
    )


def _document(
    document_id: str,
    *,
    doc_type="duplicate_bid",
    owner="user-1",
    project_id=None,
    status="parsed",
    filename=None,
    markdown_path=None,
    file_path=None,
    coverage=None,
    ordinal=None,
):
    return SimpleNamespace(
        id=document_id,
        owner_user_id=owner,
        project_id=project_id,
        doc_type=doc_type,
        status=status,
        original_filename=filename or f"{document_id}.docx",
        file_path=str(file_path or f"C:/tmp/{document_id}.docx"),
        parsed_markdown_path=(str(markdown_path) if markdown_path else None),
        parsed_html_path=None,
        parsed_images_dir=None,
        evidence_blocks_path=None,
        coverage_summary=coverage,
        duplicate_party_key=None,
        duplicate_display_name=None,
        duplicate_ordinal=ordinal,
        source_version=None,
        source_snapshot_hash=None,
        source_uri=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_release_capabilities_follow_runtime_batch_flag(monkeypatch, enabled):
    monkeypatch.setattr(
        duplicate_api,
        "get_settings",
        lambda: SimpleNamespace(duplicate_batch_enabled=enabled),
    )

    payload = await duplicate_api.get_duplicate_release_capabilities(
        SimpleNamespace(id="user-1")
    )

    assert payload["features"]["batch"] is enabled


def _batch_payload(*ids, party_keys=None, source_ids=None):
    party_keys = party_keys or [None] * len(ids)
    return DuplicateBatchAttachRequest(
        members=[
            DuplicateBatchMemberAttach(
                document_id=document_id,
                party_key=party_key,
                display_name=f"Bid {index + 1}",
                ordinal=index,
            )
            for index, (document_id, party_key) in enumerate(zip(ids, party_keys))
        ],
        source_document_ids=list(source_ids or []),
    )


@pytest.mark.parametrize("member_count", [2, 11])
def test_batch_attach_schema_enforces_three_to_ten_members(member_count):
    with pytest.raises(ValidationError):
        _batch_payload(*(f"doc-{index}" for index in range(member_count)))


@pytest.mark.asyncio
async def test_batch_attach_rejects_duplicate_party_keys(monkeypatch):
    monkeypatch.setattr(
        documents_api,
        "settings",
        SimpleNamespace(duplicate_batch_enabled=True),
    )
    project = _project()
    documents = [_document(f"doc-{index}") for index in range(3)]
    db = _SequenceDB(_Result(one=project), _Result(scalar_rows=documents))

    with pytest.raises(HTTPException) as exc:
        await documents_api.attach_duplicate_batch(
            _batch_payload("doc-0", "doc-1", "doc-2", party_keys=["same", "same", "third"]),
            project.id,
            db,
            SimpleNamespace(id="user-1"),
        )

    assert exc.value.status_code == 400
    assert "重复" in str(exc.value.detail)
    assert all(document.project_id is None for document in documents)


@pytest.mark.asyncio
async def test_batch_attach_rejects_duplicate_source_ids_before_query(monkeypatch):
    monkeypatch.setattr(
        documents_api,
        "settings",
        SimpleNamespace(duplicate_batch_enabled=True),
    )
    project = _project()
    db = _SequenceDB(_Result(one=project))

    with pytest.raises(HTTPException) as exc:
        await documents_api.attach_duplicate_batch(
            _batch_payload(
                "doc-0",
                "doc-1",
                "doc-2",
                source_ids=["source-1", "source-1"],
            ),
            project.id,
            db,
            SimpleNamespace(id="user-1"),
        )

    assert exc.value.status_code == 400
    assert "重复" in str(exc.value.detail)
    assert db.results == []


@pytest.mark.asyncio
async def test_batch_attach_rejects_member_source_overlap(monkeypatch):
    monkeypatch.setattr(
        documents_api,
        "settings",
        SimpleNamespace(duplicate_batch_enabled=True),
    )
    project = _project()
    db = _SequenceDB(_Result(one=project))

    with pytest.raises(HTTPException) as exc:
        await documents_api.attach_duplicate_batch(
            _batch_payload("doc-0", "doc-1", "doc-2", source_ids=["doc-1"]),
            project.id,
            db,
            SimpleNamespace(id="user-1"),
        )

    assert exc.value.status_code == 400
    assert "不能重复" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_batch_attach_can_store_party_metadata_and_sources(monkeypatch):
    monkeypatch.setattr(
        documents_api,
        "settings",
        SimpleNamespace(duplicate_batch_enabled=True),
    )
    project = _project()
    members = [_document(f"doc-{index}") for index in range(3)]
    source = _document(
        "source-1",
        doc_type="duplicate_tender",
        filename="tender.pdf",
    )
    db = _SequenceDB(
        _Result(one=project),
        _Result(scalar_rows=[*members, source]),
        _Result(scalar_rows=[]),
    )

    result = await documents_api.attach_duplicate_batch(
        _batch_payload(
            "doc-0",
            "doc-1",
            "doc-2",
            party_keys=["alpha", "beta", "gamma"],
            source_ids=[source.id],
        ),
        project.id,
        db,
        SimpleNamespace(id="user-1"),
    )

    assert db.committed is True
    assert [item.id for item in result] == ["doc-0", "doc-1", "doc-2", "source-1"]
    assert [item.duplicate_party_key for item in members] == ["alpha", "beta", "gamma"]
    assert [item.duplicate_ordinal for item in members] == [0, 1, 2]
    assert all(item.project_id == project.id for item in [*members, source])


@pytest.mark.asyncio
async def test_batch_start_is_fail_closed_when_feature_is_disabled(monkeypatch):
    monkeypatch.setattr(
        duplicate_api,
        "get_settings",
        lambda: SimpleNamespace(duplicate_batch_enabled=False),
    )
    project = _project()
    db = _SequenceDB(_Result(one=project), _Result(scalar_rows=[]))

    with pytest.raises(HTTPException) as exc:
        await duplicate_api.start_duplicate_check(
            request=SimpleNamespace(),
            project_id=project.id,
            db=db,
            current_user=SimpleNamespace(id="user-1"),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "DUPLICATE_BATCH_DISABLED"


@pytest.mark.asyncio
@pytest.mark.parametrize("document_count", [2, 11])
async def test_batch_start_requires_three_to_ten_documents(monkeypatch, document_count):
    monkeypatch.setattr(
        duplicate_api,
        "get_settings",
        lambda: SimpleNamespace(duplicate_batch_enabled=True),
    )
    project = _project()
    documents = [_document(f"doc-{index}") for index in range(document_count)]
    db = _SequenceDB(_Result(one=project), _Result(scalar_rows=documents))

    with pytest.raises(HTTPException) as exc:
        await duplicate_api.start_duplicate_check(
            request=SimpleNamespace(),
            project_id=project.id,
            db=db,
            current_user=SimpleNamespace(id="user-1"),
        )

    assert exc.value.status_code == 400
    assert "3-10" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_batch_start_rejects_identical_documents_before_billing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        duplicate_api,
        "get_settings",
        lambda: SimpleNamespace(duplicate_batch_enabled=True),
    )
    paths = []
    for index in range(3):
        path = tmp_path / f"bid-{index}.md"
        path.write_text("identical batch content", encoding="utf-8")
        paths.append(path)
    project = _project()
    documents = [
        _document(
            f"doc-{index}",
            markdown_path=path,
            file_path=path,
            ordinal=index,
        )
        for index, path in enumerate(paths)
    ]
    db = _SequenceDB(_Result(one=project), _Result(scalar_rows=documents))

    with pytest.raises(HTTPException) as exc:
        await duplicate_api.start_duplicate_check(
            request=SimpleNamespace(),
            project_id=project.id,
            db=db,
            current_user=SimpleNamespace(id="user-1"),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "IDENTICAL_DOCUMENTS"
    assert set(exc.value.detail["document_ids"]) == {"doc-0", "doc-1", "doc-2"}


@pytest.mark.asyncio
async def test_coverage_api_exposes_task_snapshot_and_failed_document():
    project = _project()
    task = SimpleNamespace(
        id="task-1",
        project_id=project.id,
        task_type="duplicate",
        duplicate_mode="batch",
        duplicate_algorithm_version="duplicate-s2-4.1",
        duplicate_feature_snapshot={"features": {"semantic": False}},
    )
    documents = [
        _document("doc-0", coverage={"status": "complete", "warnings": []}),
        _document(
            "doc-1",
            status="failed",
            coverage={"status": "insufficient", "warnings": ["parser_failed"]},
        ),
        _document("source-1", doc_type="duplicate_tender", coverage={"status": "complete"}),
    ]
    db = _SequenceDB(
        _Result(one=project),
        _Result(one=task),
        _Result(scalar_rows=documents),
    )

    payload = await duplicate_api.get_duplicate_coverage(
        project.id, task.id, db, SimpleNamespace(id="user-1")
    )
    response = DuplicateCoverageResponse.model_validate(payload)

    assert response.coverage_status == "insufficient"
    assert response.algorithm_version == "duplicate-s2-4.1"
    assert response.feature_snapshot["features"]["semantic"] is False
    assert any("parse_status_failed" in warning for warning in response.coverage_warnings)
    assert {item.document_id for item in response.documents} == {"doc-0", "doc-1", "source-1"}


@pytest.mark.asyncio
async def test_matrix_api_returns_members_and_pair_summaries():
    project = _project()
    task = SimpleNamespace(
        id="task-1", project_id=project.id, task_type="duplicate", duplicate_mode="batch"
    )
    documents = [
        _document("doc-0", filename="A.docx", coverage={"status": "complete"}),
        _document("doc-1", filename="B.docx", coverage={"status": "complete"}),
        _document("doc-2", filename="C.docx", coverage={"status": "partial"}),
    ]
    members = [
        SimpleNamespace(
            task_id=task.id,
            document_id=document.id,
            party_key=party,
            display_name=display,
            ordinal=index,
            member_metadata={"role": "bid"},
        )
        for index, (document, party, display) in enumerate(
            zip(documents, ["a", "b", "c"], ["Alpha", "Beta", "Gamma"])
        )
    ]
    pair = SimpleNamespace(
        id="pair-0-1",
        task_id=task.id,
        left_document_id="doc-0",
        right_document_id="doc-1",
        candidate_count=4,
        finding_count=2,
        suspicious_count=1,
        unknown_count=1,
        max_evidence_strength=0.88,
        coverage_status="complete",
        channel_hits={"lexical": 2},
    )
    db = _SequenceDB(
        _Result(one=project),
        _Result(one=task),
        _Result(scalar_rows=documents),
        _Result(scalar_rows=members),
        _Result(scalar_rows=[pair]),
    )

    payload = await duplicate_api.get_duplicate_matrix(
        project.id, task.id, db, SimpleNamespace(id="user-1")
    )
    response = DuplicateMatrixResponse.model_validate(payload)

    assert response.mode == "batch"
    assert response.coverage_status == "partial"
    assert [item.display_name for item in response.members] == ["Alpha", "Beta", "Gamma"]
    assert response.pairs[0].left_display_name == "Alpha"
    assert response.pairs[0].suspicious_count == 1


@pytest.mark.asyncio
async def test_cluster_api_returns_occurrences_with_display_names():
    project = _project()
    task = SimpleNamespace(
        id="task-1", project_id=project.id, task_type="duplicate", duplicate_mode="batch"
    )
    documents = [
        _document("doc-0", filename="A.docx"),
        _document("doc-1", filename="B.docx"),
    ]
    members = [
        SimpleNamespace(document_id="doc-0", display_name="Alpha"),
        SimpleNamespace(document_id="doc-1", display_name="Beta"),
    ]
    cluster = SimpleNamespace(
        id="cluster-1",
        task_id=task.id,
        finding_id="finding-1",
        cluster_key="hash-1",
        content_type="paragraph",
        document_ids=["doc-0", "doc-1"],
        occurrence_count=2,
        representative_excerpt="same paragraph",
        evidence_strength=0.91,
        coverage_status="complete",
        cluster_metadata={"hash": "abc"},
    )
    occurrences = [
        SimpleNamespace(
            id="occ-0",
            task_id=task.id,
            finding_id="finding-1",
            cluster_id=cluster.id,
            document_id=document.id,
            block_id=f"{document.id}:b1",
            excerpt="same paragraph",
            location={"section": "intro"},
            channel="lexical",
        )
        for document in documents
    ]
    db = _SequenceDB(
        _Result(one=project),
        _Result(one=task),
        _Result(scalar_rows=documents),
        _Result(scalar_rows=members),
        _Result(scalar_rows=[cluster]),
        _Result(scalar_rows=occurrences),
    )

    payload = await duplicate_api.get_duplicate_clusters(
        project.id,
        task.id,
        db,
        SimpleNamespace(id="user-1"),
        include_occurrences=True,
        limit=200,
    )
    response = [DuplicateClusterResponse.model_validate(item) for item in payload]

    assert response[0].occurrence_count == 2
    assert [item.display_name for item in response[0].occurrences] == ["Alpha", "Beta"]


@pytest.mark.asyncio
async def test_finding_occurrence_api_returns_display_name_and_filename():
    project = _project()
    task = SimpleNamespace(
        id="task-1", project_id=project.id, task_type="duplicate", duplicate_mode="batch"
    )
    finding = SimpleNamespace(id="finding-1", task_id=task.id)
    documents = [_document("doc-0", filename="A.docx")]
    members = [SimpleNamespace(document_id="doc-0", display_name="Alpha")]
    occurrence = SimpleNamespace(
        id="occ-1",
        task_id=task.id,
        finding_id=finding.id,
        cluster_id=None,
        document_id="doc-0",
        block_id="doc-0:b1",
        excerpt="evidence",
        location={"section": "scope"},
        channel="lexical",
    )
    db = _SequenceDB(
        _Result(one=project),
        _Result(one=task),
        _Result(one=finding),
        _Result(scalar_rows=documents),
        _Result(scalar_rows=members),
        _Result(scalar_rows=[occurrence]),
    )

    payload = await duplicate_api.get_duplicate_finding_occurrences(
        project.id, task.id, finding.id, db, SimpleNamespace(id="user-1")
    )
    response = [DuplicateOccurrenceResponse.model_validate(item) for item in payload]

    assert response[0].filename == "A.docx"
    assert response[0].display_name == "Alpha"


def test_s2_batch_and_runtime_migrations_have_required_constraints():
    migration_dir = Path(__file__).resolve().parents[1] / "migrations"
    batch_sql = (migration_dir / "029_create_duplicate_batch_matrix.sql").read_text(
        encoding="utf-8"
    )
    runtime_sql = (migration_dir / "030_add_duplicate_runtime_snapshot.sql").read_text(
        encoding="utf-8"
    )

    for table_name in (
        "duplicate_document_members",
        "duplicate_occurrences",
        "duplicate_pair_summaries",
        "duplicate_evidence_clusters",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in batch_sql
    for required in (
        "ux_duplicate_member_task_document",
        "ux_duplicate_member_task_ordinal",
        "ux_duplicate_pair_summary",
        "ux_duplicate_cluster_task_key",
        "coverage_status",
        "document_ids JSONB",
    ):
        assert required in batch_sql
    assert "duplicate_algorithm_version" in runtime_sql
    assert "duplicate_feature_snapshot JSONB" in runtime_sql
    assert "ix_review_tasks_duplicate_algorithm_version" in runtime_sql
