"""有解的时候，也把账算清楚。

求解成功只说明"能装上"，装的是什么、为什么不是更新的版本、过程中退了几次，
这些才是日常真正想知道的。这个模块把一份 :class:`~ravel.solver.Solution`
摊成一张表，外加每条选择的一句话理由。

判断"为什么不是更新的版本"不需要再求解一次，只需要看两处：最终选定的每个包
带出来的依赖，以及根需求。真正会把某个新版本排除掉的，一定是这二者之一；
两个都不是的话，那就是几个约束连在一起才排除的——这种也照实说，不装作知道。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .registry import Requirement
from .solver import Solution
from .terms import Term
from .versions import Version, VersionRange, format_constraint

MAX_REASONS = 2


def display_width(text: str) -> int:
    """中文按两格算，表格才排得齐。"""
    total = 0
    for char in text:
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def _same(left: str, right: str) -> bool:
    return left.lower() == right.lower()


@dataclass(frozen=True)
class Line:
    """表格里的一行：包、版本、以及一句解释。"""

    name: str
    version: str
    note: str


def requirements_on(solution: Solution, name: str) -> List[str]:
    """所有对这个包提出要求的地方。"""
    found: List[str] = []
    for requirement in solution.requirements:
        if _same(requirement.name, name):
            found.append(f"项目需求 {requirement}")
    for owner, version in solution.items():
        package = solution.registry.get(owner)
        if package is None:  # pragma: no cover - 解里的包一定存在
            continue
        for dep_name, constraint in package.release(version).dependencies:
            if _same(dep_name, name):
                found.append(
                    f"{owner} {version} 依赖 {dep_name} {format_constraint(constraint)}")
    return found


def excluders(solution: Solution, name: str, version: Version) -> List[str]:
    """单独一条就能把 `version` 挡在外面的约束。"""
    found: List[str] = []
    for requirement in solution.requirements:
        if _same(requirement.name, name) and not requirement.constraint.contains(version):
            found.append(f"项目需求 {requirement}")
    for owner, owner_version in solution.items():
        package = solution.registry.get(owner)
        if package is None:  # pragma: no cover
            continue
        for dep_name, constraint in package.release(owner_version).dependencies:
            if _same(dep_name, name) and not constraint.contains(version):
                found.append(
                    f"{owner} {owner_version} 依赖 {dep_name} {format_constraint(constraint)}")
    return found


def self_conflict(solution: Solution, name: str, version: Version) -> Optional[str]:
    """这个版本自己要的东西，和已经定下来的版本对不上。"""
    package = solution.registry.get(name)
    if package is None or version not in package.versions:  # pragma: no cover
        return None
    for dep_name, constraint in package.release(version).dependencies:
        picked = solution.version_of(dep_name)
        if picked is not None and not constraint.contains(picked):
            return (f"它自己要 {dep_name} {format_constraint(constraint)}，"
                    f"而 {dep_name} 已经定在 {picked}")
    return None


def why_not(solution: Solution, name: str, version: Version) -> str:
    """为什么没选这个版本——一句话。"""
    package = solution.registry.get(name)
    if package is None:  # pragma: no cover - 调用方给的名字一定在 registry 里
        return "registry 里没有这个包"
    if version in package.versions and package.release(version).yanked:
        return "这个版本已被撤回"
    blocked = excluders(solution, name, version)
    if blocked:
        return "被 " + "、".join(blocked[:MAX_REASONS]) + " 排除"
    if version.is_prerelease and not solution_version_allowed(
            solution, name).allows_prerelease(version):
        return "预发布版本要先被约束点名，这里没点名"
    own = self_conflict(solution, name, version)
    if own:
        return own
    if not solution_version_allowed(solution, name).contains(version):
        return "与其他包的版本要求连在一起就无解"
    return "本来也能选，求解器按顺序取了更早的"


def solution_version_allowed(solution: Solution, name: str) -> VersionRange:
    """在最终解里，这个包还允许哪些版本。

    解本身不保留中间状态，这里按"所有指向它的约束求交"重算一遍：
    根需求 ∪ 每个已定包的依赖，交了之后就是它最终被允许的范围。
    """
    allowed = VersionRange.any()
    for requirement in solution.requirements:
        if _same(requirement.name, name):
            allowed = allowed.intersect(requirement.constraint)
    for owner, version in solution.items():
        package = solution.registry.get(owner)
        if package is None:  # pragma: no cover
            continue
        for dep_name, constraint in package.release(version).dependencies:
            if _same(dep_name, name):
                allowed = allowed.intersect(constraint)
    return allowed


def note_for(solution: Solution, name: str, version: Version) -> str:
    """这一行为什么不是更新的版本。"""
    package = solution.registry.get(name)
    if package is None:  # pragma: no cover
        return ""
    newer = [release for release in package.releases if release.version > version]
    if not newer:
        return "已经是最新版本"
    newest = newer[0]
    return f"{newest.version} {why_not(solution, name, newest.version)}"


def lines(solution: Solution) -> List[Line]:
    return [Line(name, str(version), note_for(solution, name, version))
            for name, version in solution.items()]


def render(solution: Solution) -> str:
    """一张表 + 一行统计。"""
    rows = lines(solution)
    if not rows:
        return "求解成功：没有任何包需要安装。"
    width_name = max(display_width("包"), *(display_width(row.name) for row in rows))
    width_version = max(display_width("版本"), *(display_width(row.version) for row in rows))
    head = f"{pad('包', width_name)}  {pad('版本', width_version)}  说明"
    body = [head, "  ".join(["-" * width_name, "-" * width_version, "-" * 4])]
    for row in rows:
        body.append("  ".join([pad(row.name, width_name),
                               pad(row.version, width_version),
                               row.note]))
    stats = solution.stats
    summary = (f"共 {len(rows)} 个包：{stats.decisions} 次决策、"
               f"{stats.derivations} 次推导、{stats.backtracks} 次回退。")
    return "\n".join(body) + "\n\n" + summary


def why(solution: Solution, name: str) -> str:
    """`ravel why http` 的输出：谁要它、为什么是这一版。"""
    package = solution.registry.get(name)
    if package is None:
        raise KeyError(name)
    version = solution.version_of(package.name)
    if version is None:
        return f"{package.name} 没有被选中——这组需求里不需要它。"
    out: List[str] = [f"{package.name} {version}"]
    if package.description:
        out.append(f"  {package.description}")
    asked = requirements_on(solution, package.name)
    if asked:
        out.append("  谁要它：")
        out.extend(f"    · {item}" for item in asked)
    allowed = solution_version_allowed(solution, package.name)
    out.append(f"  最终允许的区间：{format_constraint(allowed)}")
    newer = [release for release in package.releases if release.version > version]
    if newer:
        out.append("  更新的版本为什么没选：")
        for release in newer[:MAX_REASONS + 2]:
            out.append(f"    · {release.version} {why_not(solution, package.name, release.version)}")
    else:
        out.append("  它已经是最新版本。")
    dependencies = solution.dependencies_of(package.name)
    if dependencies:
        out.append("  它自己要求的：")
        for dep_name, constraint in dependencies:
            picked = solution.version_of(dep_name)
            tail = f"→ 选中 {picked}" if picked is not None else "→ 没有选中"
            out.append(f"    · {dep_name} {format_constraint(constraint)} {tail}")
    return "\n".join(out)
