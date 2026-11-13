"""任务工作台接口：汇总RPA校验同步来的阻断待办与当前可调度集合。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.schemas import ActionResult, EntryPayload
from app.services.rpa import RpaService
from app.services.workbench import WorkbenchService

router = APIRouter(prefix="/api/workbench", tags=["任务工作台"])

workbench_service = WorkbenchService()
rpa_service = RpaService()


@router.get("")
def workbench_overview(
    status: str | None = Query(default=None, description="待办状态：待处理、已办结"),
) -> dict[str, Any]:
    """任务工作台总览：阻断待办 + 可调度集合，调度侧据此判断流程能否启动。"""
    todos = workbench_service.list_todos(status=status)
    schedulable = rpa_service.schedulable_entries()
    return {
        "待办": todos,
        "待处理数": sum(1 for todo in todos if todo.get("状态") == "待处理"),
        "可调度流程": schedulable,
    }


@router.post("/{todo_id}/actions", response_model=ActionResult)
def run_action(todo_id: int, payload: EntryPayload) -> ActionResult:
    """办结一条阻断待办；已办结的待办不重复处理。"""
    action = str(payload.values.get("action") or "").strip()
    todo, message = workbench_service.run_action(todo_id, action)
    if todo is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=todo)
