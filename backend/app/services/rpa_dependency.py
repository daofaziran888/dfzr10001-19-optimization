"""RPA等待依赖校验引擎：按「动作序列 → 等待条件」的调用链给出启停判断。

调用链定位：
    步骤列表（动作序列） → 依赖列表（等待条件） → 依赖图 → 校验结论 → 是否可启用/可调度。

检查顺序（设计决定）：先做环检测，再做超时判断。
理由：等待依赖成环时流程在结构上就无法走完，此时比较超时阈值没有意义，
所以环检测优先，超时判断只在无环的图上执行。

校验会联接五类配置：动作序列（步骤列表）、等待条件（依赖列表）、超时秒数、
结果字段、运行环境；任何一项不满足都会写进问题列表。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

CHECK_ORDER = ["结构检查", "环检测", "超时判断", "输出字段引用", "运行环境"]


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def flow_code(entry: dict[str, Any]) -> str:
    return str(entry.get("流程编号") or "")


def steps_of(entry: dict[str, Any]) -> list[dict[str, Any]]:
    steps = entry.get("步骤列表")
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def dependencies_of(entry: dict[str, Any]) -> list[dict[str, Any]]:
    deps = entry.get("依赖列表")
    return [dep for dep in deps if isinstance(dep, dict)] if isinstance(deps, list) else []


def dependency_key(dep: dict[str, Any], own_code: str) -> tuple[str, int, int, str]:
    """依赖的唯一键：同一依赖反复保存时按这个键只保留一版。"""
    return (
        str(dep.get("引用流程编号") or own_code),
        _to_int(dep.get("所在步骤")),
        _to_int(dep.get("引用步骤")),
        str(dep.get("引用输出字段") or "").strip(),
    )


def dedupe_dependencies(
    incoming: list[dict[str, Any]],
    own_code: str,
) -> tuple[list[dict[str, Any]], int]:
    """对一次提交的依赖列表去重：同一依赖反复保存只保留一版（后提交的覆盖先提交的）。

    返回 (去重后的列表, 被合并掉的重复条数)。
    """
    merged: list[dict[str, Any]] = []
    index_by_key: dict[tuple[str, int, int, str], int] = {}
    deduped = 0
    for dep in incoming:
        if not isinstance(dep, dict):
            deduped += 1
            continue
        key = dependency_key(dep, own_code)
        if key in index_by_key:
            merged[index_by_key[key]] = dict(dep)
            deduped += 1
        else:
            index_by_key[key] = len(merged)
            merged.append(dict(dep))
    return merged, deduped


def _to_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _split_fields(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "")
    for sep in ("，", "、", ";", "；"):
        text = text.replace(sep, ",")
    return [token.strip() for token in text.split(",") if token.strip()]


def build_dependency_graph(flows: list[dict[str, Any]]) -> dict[tuple[str, int], set[tuple[str, int]]]:
    """把全部流程的等待条件连成依赖图，支持跨流程引用同一输出。

    节点是 (流程编号, 步骤号)；边 被等待方 → 等待方，表示「必须先完成」。
    """
    known_steps: set[tuple[str, int]] = set()
    for flow in flows:
        code = flow_code(flow)
        for step in steps_of(flow):
            known_steps.add((code, _to_int(step.get("步骤号"))))
    graph: dict[tuple[str, int], set[tuple[str, int]]] = {node: set() for node in known_steps}
    for flow in flows:
        code = flow_code(flow)
        for dep in dependencies_of(flow):
            ref_code = str(dep.get("引用流程编号") or code)
            source = (ref_code, _to_int(dep.get("引用步骤")))
            target = (code, _to_int(dep.get("所在步骤")))
            if source in known_steps and target in known_steps:
                graph.setdefault(source, set()).add(target)
    return graph


def find_cycle_nodes(graph: dict[tuple[str, int], set[tuple[str, int]]]) -> set[tuple[str, int]]:
    """找出所有处在环上的节点（能沿依赖边走回自己的节点）。"""
    cyclic: set[tuple[str, int]] = set()
    for node in graph:
        stack = list(graph.get(node, ()))
        seen: set[tuple[str, int]] = set()
        while stack:
            current = stack.pop()
            if current == node:
                cyclic.add(node)
                break
            if current in seen:
                continue
            seen.add(current)
            stack.extend(graph.get(current, ()))
    return cyclic


def validate_flow(entry: dict[str, Any], all_flows: list[dict[str, Any]]) -> dict[str, Any]:
    """对单条流程做等待依赖校验，返回可落库的校验结论。"""
    problems: list[str] = []
    code = flow_code(entry)
    steps = steps_of(entry)
    deps = dependencies_of(entry)

    # 0. 结构检查：动作序列不能为空，每步必须声明输出字段。
    if not steps:
        problems.append("动作序列为空，无法定位等待条件的调用链")
    outputs_by_step: dict[int, str] = {}
    for step in steps:
        step_no = _to_int(step.get("步骤号"))
        output = str(step.get("输出字段") or "").strip()
        if not output:
            problems.append(f"第{step_no}步未声明输出字段")
        outputs_by_step[step_no] = output

    # 1. 环检测（先于超时判断）：等待依赖成环时必须阻止启用。
    graph = build_dependency_graph(all_flows)
    cyclic = find_cycle_nodes(graph)
    own_cycle_steps = sorted(step_no for flow, step_no in cyclic if flow == code)
    if own_cycle_steps:
        text = "、".join(f"第{no}步" for no in own_cycle_steps)
        problems.append(f"等待依赖存在环（{text}相互等待），必须阻止启用")

    # 2. 超时判断：流程级超时必须是正整数，条件级超时不能超过流程级。
    flow_timeout = _to_int(entry.get("超时秒数"))
    if flow_timeout <= 0:
        problems.append("超时秒数必须是正整数")
    for dep in deps:
        at_step = _to_int(dep.get("所在步骤"))
        raw = dep.get("超时秒数")
        if raw in (None, ""):
            continue
        dep_timeout = _to_int(raw)
        if dep_timeout <= 0:
            problems.append(f"第{at_step}步的等待条件超时秒数必须是正整数")
        elif flow_timeout > 0 and dep_timeout > flow_timeout:
            problems.append(f"第{at_step}步的等待条件超时{dep_timeout}秒超过流程总超时{flow_timeout}秒")

    # 3. 输出字段引用：等待条件必须引用动作序列中真实产出的输出字段，
    #    未引用输出字段的条件不能进入可调度集合；引用后续步骤属于先后矛盾。
    declared_results = _split_fields(entry.get("结果字段"))
    produced_fields = {output for output in outputs_by_step.values() if output}
    for field in declared_results:
        if field not in produced_fields:
            problems.append(f"结果字段「{field}」未由动作序列中的任何步骤产出")
    step_numbers = set(outputs_by_step)
    flows_by_code = {flow_code(flow): flow for flow in all_flows}
    for dep in deps:
        at_step = _to_int(dep.get("所在步骤"))
        ref_code = str(dep.get("引用流程编号") or code)
        ref_step = _to_int(dep.get("引用步骤"))
        ref_field = str(dep.get("引用输出字段") or "").strip()
        if at_step not in step_numbers:
            problems.append(f"等待条件所在的第{at_step}步不在动作序列里")
            continue
        if not ref_field:
            problems.append(f"第{at_step}步的等待条件未引用输出字段，不能进入可调度集合")
            continue
        ref_flow = flows_by_code.get(ref_code)
        if ref_flow is None:
            problems.append(f"第{at_step}步引用的流程「{ref_code}」不存在")
            continue
        ref_outputs = {
            _to_int(s.get("步骤号")): str(s.get("输出字段") or "").strip()
            for s in steps_of(ref_flow)
        }
        if ref_step not in ref_outputs:
            problems.append(f"第{at_step}步引用的第{ref_step}步不存在")
            continue
        if ref_outputs[ref_step] != ref_field:
            problems.append(
                f"第{at_step}步引用的「{ref_field}」与第{ref_step}步实际输出「{ref_outputs[ref_step]}」不一致"
            )
        if ref_code == code and ref_step >= at_step:
            problems.append(f"第{at_step}步等待第{ref_step}步的输出，先后矛盾")

    # 4. 运行环境：流程必须声明运行环境，步骤环境不得与流程环境冲突。
    env = str(entry.get("运行环境") or "").strip()
    if not env:
        problems.append("运行环境不能为空")
    for step in steps:
        step_env = str(step.get("运行环境") or "").strip()
        if env and step_env and step_env != env:
            problems.append(f"第{_to_int(step.get('步骤号'))}步的运行环境「{step_env}」与流程环境「{env}」不一致")

    return {
        "通过": not problems,
        "问题": problems,
        "检查顺序": CHECK_ORDER,
        "校验时间": now_text(),
    }
