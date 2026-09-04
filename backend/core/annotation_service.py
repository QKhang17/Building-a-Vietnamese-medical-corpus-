"""Dich vu gan nhan vang co version, audit va phan xu.

Module giu validation o cac ham thuan de unit test khong can MySQL. Tat ca
thao tac ghi entity dung optimistic locking tren ``entities_version`` cua
assignment; snapshot vang chi duoc tao mot lan va khong cho phep sua lai.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from typing import Any, Iterable

import mysql.connector


ENTITY_TYPES = (
    "Bệnh lý",
    "Triệu chứng",
    "Điều trị",
    "Xét nghiệm",
    "Hình ảnh",
    "Sinh lý",
)

ENTITY_SOURCES = {"human", "dictionary", "ai", "ai+dictionary", "adjudicator"}
ENTITY_DECISIONS = {"proposed", "accepted", "modified", "added", "rejected"}
ACTIVE_DECISIONS = ENTITY_DECISIONS - {"rejected"}

ASSIGNMENT_TRANSITIONS = {
    "assigned": {"in_progress"},
    "in_progress": {"submitted"},
    "submitted": {"conflict", "locked"},
    "conflict": {"in_progress", "adjudicated", "locked"},
    "adjudicated": {"locked"},
    "locked": set(),
}


class AnnotationDatabaseNotConfigured(RuntimeError):
    pass


class AnnotationConflictError(RuntimeError):
    """Client dang ghi tren mot version da cu."""


class AnnotationPermissionError(RuntimeError):
    pass


_db_config: dict[str, Any] = {}


def configure(db_config: dict[str, Any]) -> None:
    global _db_config
    _db_config = dict(db_config)


@contextmanager
def mysql_connection():
    if not _db_config:
        raise AnnotationDatabaseNotConfigured("Chưa cấu hình cơ sở dữ liệu gán nhãn")
    conn = mysql.connector.connect(**_db_config)
    try:
        yield conn
    finally:
        conn.close()


def validate_transition(current: str, target: str) -> None:
    if target == current:
        return
    if target not in ASSIGNMENT_TRANSITIONS.get(current, set()):
        raise ValueError(f"Chuyển trạng thái không hợp lệ: {current} -> {target}")


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} phải là số nguyên")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} phải là số nguyên") from exc
    return result


def normalize_entities(text: str, entities: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chuan hoa va kiem tra entity theo offset tren van ban goc."""
    normalized: list[dict[str, Any]] = []
    seen_client_ids: set[str] = set()
    seen_active_spans: set[tuple[int, int, str]] = set()

    for raw in entities:
        if not isinstance(raw, dict):
            raise ValueError("Mỗi entity phải là một object")
        start = _as_int(raw.get("start"), "start")
        end = _as_int(raw.get("end"), "end")
        if start < 0 or end <= start or end > len(text):
            raise ValueError(f"Offset không hợp lệ: [{start}, {end})")

        surface = str(raw.get("surface") or text[start:end])
        if surface != text[start:end]:
            raise ValueError(
                f"Surface không khớp văn bản gốc tại [{start}, {end}): {surface!r}"
            )

        entity_type = str(raw.get("type") or "").strip()
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"Loại thực thể không hợp lệ: {entity_type!r}")

        source = str(raw.get("source") or "human").strip()
        if source not in ENTITY_SOURCES:
            raise ValueError(f"Nguồn thực thể không hợp lệ: {source!r}")
        decision = str(raw.get("decision") or "accepted").strip()
        if decision not in ENTITY_DECISIONS:
            raise ValueError(f"Quyết định không hợp lệ: {decision!r}")

        client_id = str(raw.get("client_id") or uuid.uuid4())
        if client_id in seen_client_ids:
            raise ValueError(f"client_id bị trùng: {client_id}")
        seen_client_ids.add(client_id)

        if decision in ACTIVE_DECISIONS:
            identity = (start, end, entity_type)
            if identity in seen_active_spans:
                raise ValueError(f"Entity bị trùng tại [{start}, {end})/{entity_type}")
            seen_active_spans.add(identity)

        normalized.append(
            {
                "client_id": client_id,
                "start": start,
                "end": end,
                "surface": surface,
                "type": entity_type,
                "code": str(raw.get("code") or "").strip()[:120],
                "source": source,
                "decision": decision,
                "reason": str(raw.get("reason") or "").strip()[:500],
                "version": max(1, _as_int(raw.get("version", 1), "version")),
            }
        )

    active = sorted(
        (item for item in normalized if item["decision"] in ACTIVE_DECISIONS),
        key=lambda item: (item["start"], item["end"], item["type"]),
    )
    for index, left in enumerate(active):
        for right in active[index + 1 :]:
            if right["start"] >= left["end"]:
                break
            if right["start"] < left["end"] and right["end"] > left["start"]:
                raise ValueError(
                    "Cấu hình hiện tại không cho phép entity chồng lấn: "
                    f"[{left['start']}, {left['end']}) và [{right['start']}, {right['end']})"
                )

    return sorted(normalized, key=lambda item: (item["start"], item["end"], item["type"]))


def entity_signature(entities: Iterable[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        sorted(
            (
                int(item["start"]),
                int(item["end"]),
                str(item["type"]),
                str(item.get("code") or ""),
            )
            for item in entities
            if str(item.get("decision") or "accepted") in ACTIVE_DECISIONS
        )
    )


def snapshot_sha256(entities: Iterable[dict[str, Any]]) -> str:
    payload = json.dumps(
        list(entities), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def visible_preannotation(annotation_mode: str, payload: Any) -> list[dict[str, Any]]:
    """Blind/adjudication khong bao gio nhan pre-label qua API."""
    return _json_load(payload, []) if annotation_mode == "assisted" else []


def can_view_independent_submissions(assignment_role: str, document_status: str) -> bool:
    return assignment_role == "adjudicator" and document_status in {
        "conflict",
        "adjudicated",
        "locked",
    }


def validate_submission_entities(
    annotation_mode: str, entities: Iterable[dict[str, Any]]
) -> None:
    rows = list(entities)
    if annotation_mode == "assisted" and any(
        str(item.get("decision") or "proposed") == "proposed" for item in rows
    ):
        raise ValueError("Assignment assisted còn nhãn proposed chưa được quyết định")
    missing_reason = [
        item
        for item in rows
        if str(item.get("decision") or "") in {"modified", "rejected"}
        and not str(item.get("reason") or "").strip()
    ]
    if missing_reason:
        raise ValueError("Nhãn modified/rejected phải có lý do")


def _entity_rows(cursor, assignment_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        "SELECT client_id,start_offset AS start,end_offset AS end,surface,"
        "entity_type AS type,concept_code AS code,entity_source AS source,"
        "decision,reason,version FROM annotation_entities "
        "WHERE assignment_id=%s ORDER BY start_offset,end_offset,entity_type",
        (assignment_id,),
    )
    return cursor.fetchall()


def list_assignments(
    expert_id: int,
    *,
    project_id: int | None = None,
    role: str | None = None,
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    where = ["aa.expert_id=%s"]
    params: list[Any] = [expert_id]
    if project_id is not None:
        where.append("ad.project_id=%s")
        params.append(project_id)
    if role:
        where.append("aa.assignment_role=%s")
        params.append(role)
    if status:
        where.append("aa.status=%s")
        params.append(status)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            base = (
                " FROM annotation_assignments aa "
                "JOIN annotation_documents ad ON ad.id=aa.document_id "
                "JOIN annotation_projects ap ON ap.id=ad.project_id "
                "JOIN articles a ON a.id=ad.article_id WHERE " + " AND ".join(where)
            )
            cursor.execute("SELECT COUNT(*) AS total" + base, params)
            total = int(cursor.fetchone()["total"])
            cursor.execute(
                "SELECT aa.id,aa.assignment_role,aa.annotation_mode,aa.status,"
                "aa.entities_version,aa.active_seconds,ad.id AS document_id,"
                "ad.split_name,ad.document_status,ad.text_sha256,"
                "ap.id AS project_id,ap.name AS project_name,ap.status AS project_status,"
                "a.id AS article_id,"
                "a.title,a.publication_year" + base +
                " ORDER BY FIELD(ad.split_name,'pilot','development','test'),ad.id "
                "LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            items = cursor.fetchall()
        finally:
            cursor.close()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _load_assignment_for_update(cursor, assignment_id: int) -> dict[str, Any]:
    cursor.execute(
        "SELECT aa.*,ad.project_id,ad.article_id,ad.document_status,ad.split_name,"
        "ap.status AS project_status,"
        "a.abstract AS source_text,a.title,a.source_url,a.publication_year "
        "FROM annotation_assignments aa "
        "JOIN annotation_documents ad ON ad.id=aa.document_id "
        "JOIN annotation_projects ap ON ap.id=ad.project_id "
        "JOIN articles a ON a.id=ad.article_id WHERE aa.id=%s FOR UPDATE",
        (assignment_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError("Không tìm thấy assignment")
    row["source_text"] = str(row.get("source_text") or "")
    return row


def get_assignment(expert_id: int, assignment_id: int) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT aa.*,ad.project_id,ad.article_id,ad.document_status,ad.split_name,"
                "ad.text_sha256,ap.name AS project_name,ap.schema_version,"
                "ap.guideline_version,ap.status AS project_status,a.title,a.abstract AS source_text,a.source_url,"
                "a.publication_year FROM annotation_assignments aa "
                "JOIN annotation_documents ad ON ad.id=aa.document_id "
                "JOIN annotation_projects ap ON ap.id=ad.project_id "
                "JOIN articles a ON a.id=ad.article_id WHERE aa.id=%s AND aa.expert_id=%s",
                (assignment_id, expert_id),
            )
            assignment = cursor.fetchone()
            if not assignment:
                raise AnnotationPermissionError("Bạn không được truy cập assignment này")
            assignment["source_text"] = str(assignment.get("source_text") or "")
            assignment["entities"] = _entity_rows(cursor, assignment_id)
            assignment["preannotation"] = visible_preannotation(
                assignment["annotation_mode"], assignment.pop("preannotation_json", None)
            )
            assignment["submissions"] = []
            if can_view_independent_submissions(
                assignment["assignment_role"], assignment["document_status"]
            ):
                cursor.execute(
                    "SELECT aa.id,aa.expert_id,e.full_name AS expert_name,aa.annotation_mode,"
                    "aa.status,aa.active_seconds FROM annotation_assignments aa "
                    "JOIN mednlp_experts e ON e.id=aa.expert_id "
                    "WHERE aa.document_id=%s AND aa.assignment_role='annotator' "
                    "AND aa.status IN ('submitted','conflict','adjudicated','locked') "
                    "ORDER BY aa.id",
                    (assignment["document_id"],),
                )
                for submission in cursor.fetchall():
                    submission["entities"] = _entity_rows(cursor, int(submission["id"]))
                    assignment["submissions"].append(submission)
            cursor.execute(
                "SELECT entities_json,snapshot_sha256,locked_at FROM annotation_gold_snapshots "
                "WHERE document_id=%s",
                (assignment["document_id"],),
            )
            gold = cursor.fetchone()
            if gold:
                gold["entities"] = _json_load(gold.pop("entities_json"), [])
            assignment["gold_snapshot"] = gold
            return assignment
        finally:
            cursor.close()


def _insert_audit(
    cursor,
    assignment: dict[str, Any],
    expert_id: int,
    event_type: str,
    before: Any = None,
    after: Any = None,
    metadata: Any = None,
) -> None:
    cursor.execute(
        "INSERT INTO annotation_audit_events(project_id,document_id,assignment_id,"
        "expert_id,event_type,before_json,after_json,metadata_json) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            assignment["project_id"],
            assignment["document_id"],
            assignment.get("id"),
            expert_id,
            event_type,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
            json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
        ),
    )


def start_assignment(expert_id: int, assignment_id: int) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            assignment = _load_assignment_for_update(cursor, assignment_id)
            if int(assignment["expert_id"]) != int(expert_id):
                raise AnnotationPermissionError("Bạn không được mở assignment này")
            if assignment["project_status"] != "active":
                raise AnnotationPermissionError("Project chưa ở trạng thái active")
            if (
                assignment["annotation_mode"] == "assisted"
                and assignment.get("preannotation_json") is None
            ):
                raise AnnotationPermissionError(
                    "Assignment assisted chưa có preannotation đã khóa"
                )
            if (
                assignment["assignment_role"] == "adjudicator"
                and assignment["document_status"] != "conflict"
                and assignment["status"] not in {"adjudicated", "locked"}
            ):
                raise AnnotationPermissionError(
                    "Chuyên gia phân xử chỉ được mở sau khi hai bản độc lập đã tạo xung đột"
                )
            if assignment["status"] == "assigned":
                validate_transition("assigned", "in_progress")
                cursor.execute(
                    "UPDATE annotation_assignments SET status='in_progress',"
                    "started_at=COALESCE(started_at,CURRENT_TIMESTAMP) WHERE id=%s",
                    (assignment_id,),
                )
                cursor.execute(
                    "UPDATE annotation_documents SET document_status='in_progress' "
                    "WHERE id=%s AND document_status='assigned'",
                    (assignment["document_id"],),
                )
                _insert_audit(cursor, assignment, expert_id, "start_assignment")
                conn.commit()
        finally:
            cursor.close()
    return get_assignment(expert_id, assignment_id)


def _replace_entity_rows(cursor, assignment_id: int, entities: list[dict[str, Any]]) -> None:
    cursor.execute("DELETE FROM annotation_entities WHERE assignment_id=%s", (assignment_id,))
    if not entities:
        return
    cursor.executemany(
        "INSERT INTO annotation_entities(assignment_id,client_id,start_offset,end_offset,"
        "surface,entity_type,concept_code,entity_source,decision,reason,version) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [
            (
                assignment_id,
                item["client_id"],
                item["start"],
                item["end"],
                item["surface"],
                item["type"],
                item["code"],
                item["source"],
                item["decision"],
                item["reason"],
                item["version"],
            )
            for item in entities
        ],
    )


def save_entities(
    expert_id: int,
    assignment_id: int,
    expected_version: int,
    entities: Iterable[dict[str, Any]],
    *,
    active_seconds_delta: int = 0,
    event_type: str = "save_entities",
) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            assignment = _load_assignment_for_update(cursor, assignment_id)
            if int(assignment["expert_id"]) != int(expert_id):
                raise AnnotationPermissionError("Bạn không được sửa assignment này")
            if assignment["status"] not in {"assigned", "in_progress", "conflict"}:
                raise ValueError(f"Assignment ở trạng thái {assignment['status']} nên không thể sửa")
            if int(assignment["entities_version"]) != int(expected_version):
                raise AnnotationConflictError(
                    f"Version hiện tại là {assignment['entities_version']}, không phải {expected_version}"
                )
            normalized = normalize_entities(assignment["source_text"], entities)
            before = _entity_rows(cursor, assignment_id)
            _replace_entity_rows(cursor, assignment_id, normalized)
            next_version = int(assignment["entities_version"]) + 1
            delta = max(0, min(int(active_seconds_delta), 4 * 60 * 60))
            next_status = "in_progress" if assignment["status"] in {"assigned", "conflict"} else assignment["status"]
            cursor.execute(
                "UPDATE annotation_assignments SET entities_version=%s,status=%s,"
                "started_at=COALESCE(started_at,CURRENT_TIMESTAMP),"
                "active_seconds=active_seconds+%s WHERE id=%s",
                (next_version, next_status, delta, assignment_id),
            )
            cursor.execute(
                "UPDATE annotation_documents SET document_status='in_progress' "
                "WHERE id=%s AND document_status IN ('assigned','conflict')",
                (assignment["document_id"],),
            )
            _insert_audit(
                cursor,
                assignment,
                expert_id,
                event_type,
                before,
                normalized,
                {"expected_version": expected_version, "new_version": next_version, "active_seconds_delta": delta},
            )
            conn.commit()
            return {"entities": normalized, "entities_version": next_version, "status": next_status}
        finally:
            cursor.close()


def undo_last_change(expert_id: int, assignment_id: int, expected_version: int) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            assignment = _load_assignment_for_update(cursor, assignment_id)
            if int(assignment["expert_id"]) != int(expert_id):
                raise AnnotationPermissionError("Bạn không được hoàn tác assignment này")
            if int(assignment["entities_version"]) != int(expected_version):
                raise AnnotationConflictError("Assignment đã thay đổi ở một phiên khác")
            cursor.execute(
                "SELECT id,before_json FROM annotation_audit_events WHERE assignment_id=%s "
                "AND event_type IN ('save_entities','undo_entities') AND before_json IS NOT NULL "
                "ORDER BY id DESC LIMIT 1",
                (assignment_id,),
            )
            event = cursor.fetchone()
            if not event:
                raise ValueError("Không có thay đổi nào để hoàn tác")
            previous = normalize_entities(
                assignment["source_text"], _json_load(event["before_json"], [])
            )
            current = _entity_rows(cursor, assignment_id)
            _replace_entity_rows(cursor, assignment_id, previous)
            next_version = int(expected_version) + 1
            cursor.execute(
                "UPDATE annotation_assignments SET entities_version=%s,status='in_progress' WHERE id=%s",
                (next_version, assignment_id),
            )
            _insert_audit(
                cursor,
                assignment,
                expert_id,
                "undo_entities",
                current,
                previous,
                {"undone_event_id": event["id"], "new_version": next_version},
            )
            conn.commit()
            return {"entities": previous, "entities_version": next_version, "status": "in_progress"}
        finally:
            cursor.close()


def _auto_lock_if_identical(cursor, assignment: dict[str, Any], expert_id: int) -> bool:
    cursor.execute(
        "SELECT id FROM annotation_assignments WHERE document_id=%s "
        "AND assignment_role='annotator' AND status='submitted' ORDER BY id",
        (assignment["document_id"],),
    )
    rows = cursor.fetchall()
    if len(rows) < 2:
        return False
    entity_sets = [_entity_rows(cursor, int(row["id"])) for row in rows]
    if any(entity_signature(entity_sets[0]) != entity_signature(items) for items in entity_sets[1:]):
        cursor.execute(
            "UPDATE annotation_documents SET document_status='conflict' WHERE id=%s",
            (assignment["document_id"],),
        )
        cursor.execute(
            "UPDATE annotation_assignments SET status='conflict' WHERE document_id=%s "
            "AND assignment_role='adjudicator' AND status='assigned'",
            (assignment["document_id"],),
        )
        return False

    gold = [item for item in entity_sets[0] if item["decision"] in ACTIVE_DECISIONS]
    digest = snapshot_sha256(gold)
    cursor.execute(
        "SELECT expert_id FROM annotation_assignments WHERE document_id=%s "
        "AND assignment_role='adjudicator' ORDER BY id LIMIT 1",
        (assignment["document_id"],),
    )
    adjudicator = cursor.fetchone()
    if not adjudicator:
        raise ValueError("Thiếu assignment phân xử nên chưa thể khóa bản vàng")
    cursor.execute(
        "INSERT IGNORE INTO annotation_gold_snapshots(document_id,adjudicator_id,entities_json,"
        "snapshot_sha256,lock_source) VALUES(%s,%s,%s,%s,'auto_identical')",
        (
            assignment["document_id"],
            adjudicator["expert_id"],
            json.dumps(gold, ensure_ascii=False),
            digest,
        ),
    )
    cursor.execute(
        "UPDATE annotation_documents SET document_status='locked' WHERE id=%s",
        (assignment["document_id"],),
    )
    cursor.execute(
        "UPDATE annotation_assignments SET status='locked' WHERE document_id=%s",
        (assignment["document_id"],),
    )
    return True


def submit_assignment(expert_id: int, assignment_id: int, expected_version: int) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            assignment = _load_assignment_for_update(cursor, assignment_id)
            if int(assignment["expert_id"]) != int(expert_id):
                raise AnnotationPermissionError("Bạn không được nộp assignment này")
            if assignment["assignment_role"] != "annotator":
                raise ValueError("Assignment phân xử phải dùng thao tác khóa bản vàng")
            if int(assignment["entities_version"]) != int(expected_version):
                raise AnnotationConflictError("Assignment đã thay đổi ở một phiên khác")
            if assignment["status"] != "in_progress":
                raise ValueError("Chỉ assignment đang thực hiện mới có thể nộp")
            validate_submission_entities(
                assignment["annotation_mode"], _entity_rows(cursor, assignment_id)
            )
            validate_transition("in_progress", "submitted")
            cursor.execute(
                "UPDATE annotation_assignments SET status='submitted',submitted_at=CURRENT_TIMESTAMP "
                "WHERE id=%s",
                (assignment_id,),
            )
            cursor.execute(
                "UPDATE annotation_documents SET document_status='submitted' WHERE id=%s",
                (assignment["document_id"],),
            )
            locked = _auto_lock_if_identical(cursor, assignment, expert_id)
            _insert_audit(
                cursor,
                assignment,
                expert_id,
                "submit_assignment",
                metadata={
                    "auto_locked": locked,
                    "lock_source": "auto_identical" if locked else None,
                },
            )
            conn.commit()
            return {"status": "locked" if locked else "submitted", "auto_locked": locked}
        finally:
            cursor.close()


def lock_adjudication(expert_id: int, assignment_id: int, expected_version: int) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            assignment = _load_assignment_for_update(cursor, assignment_id)
            if int(assignment["expert_id"]) != int(expert_id) or assignment["assignment_role"] != "adjudicator":
                raise AnnotationPermissionError("Bạn không phải chuyên gia phân xử của tài liệu này")
            if int(assignment["entities_version"]) != int(expected_version):
                raise AnnotationConflictError("Assignment đã thay đổi ở một phiên khác")
            if assignment["document_status"] != "conflict" or assignment["status"] not in {"conflict", "in_progress"}:
                raise ValueError("Tài liệu chưa sẵn sàng để phân xử")
            entities = [item for item in _entity_rows(cursor, assignment_id) if item["decision"] in ACTIVE_DECISIONS]
            entities = normalize_entities(assignment["source_text"], entities)
            digest = snapshot_sha256(entities)
            cursor.execute(
                "INSERT INTO annotation_gold_snapshots(document_id,adjudicator_id,entities_json,"
                "snapshot_sha256,lock_source) VALUES(%s,%s,%s,%s,'human_adjudication')",
                (assignment["document_id"], expert_id, json.dumps(entities, ensure_ascii=False), digest),
            )
            cursor.execute(
                "UPDATE annotation_documents SET document_status='locked' WHERE id=%s",
                (assignment["document_id"],),
            )
            cursor.execute(
                "UPDATE annotation_assignments SET status='locked',submitted_at=COALESCE(submitted_at,CURRENT_TIMESTAMP) "
                "WHERE document_id=%s",
                (assignment["document_id"],),
            )
            _insert_audit(
                cursor,
                assignment,
                expert_id,
                "lock_adjudication",
                after=entities,
                metadata={"snapshot_sha256": digest, "lock_source": "human_adjudication"},
            )
            conn.commit()
            return {"status": "locked", "snapshot_sha256": digest, "entities": entities}
        except mysql.connector.IntegrityError as exc:
            raise AnnotationConflictError("Bản vàng đã được khóa trước đó") from exc
        finally:
            cursor.close()
