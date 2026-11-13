"""任务工作台业务规则：承接RPA校验结果，生成与办结阻断待办。

同一流程同一时间只保留一条待处理的阻断待办：校验反复失败时更新内容，
校验通过后自动办结，不会堆出一串重复待办。
"""
from __future__ import annotations

from typing import Any

from app.services.rpa_dependency import now_text
from app.store import store

MODULE = "workbench"
ACTION_RULES = {"办结": ("待处理", "已办结")}


class WorkbenchService:
    def list_todos(self, *, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        return sorted(rows, key=lambda row: int(row.get("id", 0)))

    def sync_validation(self, entry: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
        """把一次RPA校验结果同步到工作台：失败生成/更新阻断待办，通过则办结。"""
        code = str(entry.get("流程编号") or "")
        if not code:
            return None
        rows = store.rows(MODULE)
        open_todos = [
            row for row in rows
            if row.get("关联流程") == code and row.get("状态") == "待处理"
        ]
        if result.get("通过"):
            for todo in open_todos:
                self._close(todo, "校验通过，自动办结")
            return None
        content = "；".join(str(item) for item in result.get("问题") or ["校验未通过"])
        if open_todos:
            todo = open_todos[0]
            todo["内容"] = content
            todo["生成时间"] = now_text()
            for extra in open_todos[1:]:
                self._close(extra, "同一流程只保留一条阻断待办")
            return todo
        todo = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "待办编号": f"TODO-{max((int(row.get('id', 0)) for row in rows), default=0) + 1:04d}",
            "关联流程": code,
            "待办类型": "阻断",
            "内容": content,
            "状态": "待处理",
            "生成时间": now_text(),
            "处理说明": "",
            "pending": True,
            "abnormal": True,
        }
        rows.append(todo)
        return todo

    def run_action(self, todo_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        todo = store.find(MODULE, todo_id)
        if todo is None:
            return None, f"待办 {todo_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于任务工作台可执行范围"
        source, target = ACTION_RULES[action]
        if todo.get("状态") != source:
            return None, f"待办当前状态「{todo.get('状态')}」不允许{action}"
        self._close(todo, "人工办结")
        return todo, f"待办已{action}"

    @staticmethod
    def _close(todo: dict[str, Any], note: str) -> None:
        todo["状态"] = "已办结"
        todo["处理说明"] = note
        todo["pending"] = False
        todo["abnormal"] = False
