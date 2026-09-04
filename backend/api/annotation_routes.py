"""API cho quy trinh gan nhan vang va phan xu."""

from __future__ import annotations

from typing import Literal

import mysql.connector
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from core.annotation_service import (
    AnnotationConflictError,
    AnnotationDatabaseNotConfigured,
    AnnotationPermissionError,
    configure,
    get_assignment,
    list_assignments,
    lock_adjudication,
    save_entities,
    start_assignment,
    submit_assignment,
    undo_last_change,
)
from core.expert_service import decode_token


router = APIRouter(prefix="/api/annotation", tags=["annotation-research"])


def init_annotation_router(db_config: dict) -> None:
    configure(db_config)


def _require_expert(authorization: str | None) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Vui lòng đăng nhập chuyên gia")
    try:
        return decode_token(authorization.split(" ", 1)[1].strip())
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc


class EntityPayload(BaseModel):
    client_id: str | None = None
    start: int
    end: int
    surface: str = ""
    type: str
    code: str = ""
    source: str = "human"
    decision: str = "accepted"
    reason: str = ""
    version: int = Field(default=1, ge=1)


class SaveEntitiesRequest(BaseModel):
    expected_version: int = Field(ge=1)
    entities: list[EntityPayload]
    active_seconds_delta: int = Field(default=0, ge=0, le=14400)


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


def _call(action):
    try:
        return action()
    except AnnotationPermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except AnnotationConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except AnnotationDatabaseNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except mysql.connector.Error as exc:
        if exc.errno == 1146:
            raise HTTPException(
                503,
                "Chưa có schema nghiên cứu. Hãy chạy backend/sql/annotation_research.sql.",
            ) from exc
        raise HTTPException(500, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/assignments")
def assignment_list(
    project_id: int | None = None,
    role: Literal["annotator", "adjudicator"] | None = None,
    status: Literal[
        "assigned", "in_progress", "submitted", "conflict", "adjudicated", "locked"
    ]
    | None = None,
    limit: int = 200,
    offset: int = 0,
    authorization: str | None = Header(default=None),
):
    expert = _require_expert(authorization)
    return _call(
        lambda: list_assignments(
            int(expert["sub"]),
            project_id=project_id,
            role=role,
            status=status,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/assignments/{assignment_id}")
def assignment_detail(
    assignment_id: int,
    authorization: str | None = Header(default=None),
):
    expert = _require_expert(authorization)
    return _call(lambda: get_assignment(int(expert["sub"]), assignment_id))


@router.post("/assignments/{assignment_id}/start")
def assignment_start(
    assignment_id: int,
    authorization: str | None = Header(default=None),
):
    expert = _require_expert(authorization)
    return _call(lambda: start_assignment(int(expert["sub"]), assignment_id))


@router.put("/assignments/{assignment_id}/entities")
def assignment_save_entities(
    assignment_id: int,
    req: SaveEntitiesRequest,
    authorization: str | None = Header(default=None),
):
    expert = _require_expert(authorization)
    payload = [item.model_dump() for item in req.entities]
    return _call(
        lambda: save_entities(
            int(expert["sub"]),
            assignment_id,
            req.expected_version,
            payload,
            active_seconds_delta=req.active_seconds_delta,
        )
    )


@router.post("/assignments/{assignment_id}/undo")
def assignment_undo(
    assignment_id: int,
    req: VersionRequest,
    authorization: str | None = Header(default=None),
):
    expert = _require_expert(authorization)
    return _call(
        lambda: undo_last_change(int(expert["sub"]), assignment_id, req.expected_version)
    )


@router.post("/assignments/{assignment_id}/submit")
def assignment_submit(
    assignment_id: int,
    req: VersionRequest,
    authorization: str | None = Header(default=None),
):
    expert = _require_expert(authorization)
    return _call(
        lambda: submit_assignment(int(expert["sub"]), assignment_id, req.expected_version)
    )


@router.post("/assignments/{assignment_id}/lock-adjudication")
def adjudication_lock(
    assignment_id: int,
    req: VersionRequest,
    authorization: str | None = Header(default=None),
):
    expert = _require_expert(authorization)
    return _call(
        lambda: lock_adjudication(int(expert["sub"]), assignment_id, req.expected_version)
    )
