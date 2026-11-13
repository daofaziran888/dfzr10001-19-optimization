"""RPA流程配置业务规则：状态流转、等待依赖校验、版本快照与可调度集合。

启停判断的调用链：动作序列 → 等待条件 → 依赖校验引擎（app.services.rpa_dependency）
→ 校验结论 → 是否允许启用、是否进入可调度集合。

状态只能按 草稿 → 待校验 → 已启用 → 已停用 推进；启用时生成配置版本快照，
历史运行版本继续沿用原配置，已启动的运行实例仍按原版本完成。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services import rpa_dependency
from app.services.workbench import WorkbenchService
from app.store import store

MODULE = "rpa"
REQUIRED_FIELDS = ["流程编号", "流程名称", "输入来源"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
# 动作 → (允许的前置状态, 目标状态)：只允许沿状态序列推进，不允许跳级或回退。
ACTION_RULES = {
    "提交校验": ("草稿", "待校验"),
    "启用配置": ("待校验", "已启用"),
    "停用配置": ("已启用", "已停用"),
}
NEGATIVE_ACTIONS = ["停用配置"]
EDITABLE_STATUS = {"草稿", "待校验"}

workbench = WorkbenchService()


class RpaService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("流程编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        schedulable = {item["流程编号"] for item in self.schedulable_entries()}
        return [self.decorate(row, schedulable) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        schedulable = {item["流程编号"] for item in self.schedulable_entries()}
        return self.decorate(entry, schedulable)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ("动作序列", "等待条件", "超时秒数", "结果字段", "运行环境"):
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["步骤列表"] = []
        entry["依赖列表"] = []
        entry["配置版本"] = []
        entry["运行实例"] = []
        entry["校验结果"] = None
        rows.append(entry)
        return entry, []

    def save_dependencies(
        self, entry_id: int, incoming: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, str]:
        """保存等待依赖：整单替换，同一依赖反复保存只保留一版，保存后立即重跑校验。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"RPA流程 {entry_id} 不存在或已归档"
        if entry.get("status") not in EDITABLE_STATUS:
            return None, (
                f"当前状态「{entry.get('status')}」不允许保存依赖，"
                "已启用流程的历史版本继续沿用原配置，请先停用再调整"
            )
        merged, deduped = rpa_dependency.dedupe_dependencies(
            incoming, str(entry.get("流程编号") or "")
        )
        entry["依赖列表"] = merged
        result = self.validate(entry)
        message = (
            f"已保存依赖：生效 {len(merged)} 条、去重 {deduped} 条；"
            f"校验{'通过' if result['通过'] else '未通过：' + '；'.join(result['问题'])}"
        )
        return entry, message

    def validate(self, entry: dict[str, Any]) -> dict[str, Any]:
        """重跑依赖校验，落库校验结论并同步任务工作台。"""
        result = rpa_dependency.validate_flow(entry, store.rows(MODULE))
        entry["校验结果"] = result
        entry["abnormal"] = not result["通过"]
        workbench.sync_validation(entry, result)
        return result

    def validate_all(self) -> None:
        """启动巡检：把存量流程全部校验一遍，让工作台先看到阻断待办。"""
        for entry in store.rows(MODULE):
            self.validate(entry)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"RPA流程 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于RPA流程配置可执行范围"
        source, target = ACTION_RULES[action]
        if entry.get("status") != source:
            return None, (
                f"当前状态「{entry.get('status')}」不允许{action}，"
                f"状态只能按{'→'.join(STATUS_ORDER)}推进"
            )
        if action == "提交校验":
            result = self.validate(entry)
            self._move(entry, target, action)
            if result["通过"]:
                return entry, "RPA流程已提交校验，等待依赖校验通过"
            return entry, "RPA流程已提交校验，校验未通过：" + "；".join(result["问题"])
        if action == "启用配置":
            result = self.validate(entry)
            if not result["通过"]:
                return None, (
                    "启用被阻止：" + "；".join(result["问题"]) + "（已生成阻断待办）"
                )
            version_no = self._snapshot_version(entry)
            self._move(entry, target, action)
            return entry, f"RPA流程已启用配置，当前版本 {version_no}，已进入可调度集合"
        self._move(entry, target, action)
        return entry, f"RPA流程已{action}"

    def schedulable_entries(self) -> list[dict[str, Any]]:
        """可调度集合：已启用且等待依赖校验通过的流程；未引用输出字段的条件进不来。"""
        schedulable: list[dict[str, Any]] = []
        for entry in store.rows(MODULE):
            if entry.get("status") != "已启用":
                continue
            result = rpa_dependency.validate_flow(entry, store.rows(MODULE))
            if not result["通过"]:
                continue
            schedulable.append({
                "流程编号": entry.get("流程编号"),
                "流程名称": entry.get("流程名称"),
                "当前版本": self._current_version(entry),
                "运行环境": entry.get("运行环境"),
                "超时秒数": entry.get("超时秒数"),
                "结果字段": entry.get("结果字段"),
            })
        return schedulable

    def decorate(
        self, entry: dict[str, Any] | None, schedulable: set[str] | None = None
    ) -> dict[str, Any] | None:
        if entry is None:
            return None
        result = entry.get("校验结果")
        if not result:
            entry["校验结论"] = "未校验"
        else:
            entry["校验结论"] = "通过" if result.get("通过") else "未通过"
        entry["当前版本"] = self._current_version(entry)
        if schedulable is None:
            schedulable = {item["流程编号"] for item in self.schedulable_entries()}
        entry["可调度"] = "是" if entry.get("流程编号") in schedulable else "否"
        return entry

    def _move(self, entry: dict[str, Any], target: str, action: str) -> None:
        entry["status"] = target
        entry["pending"] = target in STATUS_ORDER[:2]
        if action in NEGATIVE_ACTIONS:
            entry["abnormal"] = True

    @staticmethod
    def _current_version(entry: dict[str, Any]) -> str:
        versions = entry.get("配置版本") or []
        return str(versions[-1].get("版本号")) if versions else "—"

    @staticmethod
    def _snapshot_version(entry: dict[str, Any]) -> str:
        """启用时固化一份配置快照；历史运行版本继续沿用原配置。"""
        versions = entry.setdefault("配置版本", [])
        version_no = f"v{len(versions) + 1}"
        versions.append({
            "版本号": version_no,
            "启用时间": rpa_dependency.now_text(),
            "快照": {
                "步骤列表": deepcopy(entry.get("步骤列表") or []),
                "依赖列表": deepcopy(entry.get("依赖列表") or []),
                "超时秒数": entry.get("超时秒数"),
                "结果字段": entry.get("结果字段"),
                "运行环境": entry.get("运行环境"),
            },
        })
        return version_no
