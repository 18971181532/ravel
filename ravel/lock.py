"""锁文件：把一次求解的结果钉下来，并在下一次检查它是否还成立。

registry 是会变的——某个版本被撤回、某个包被删掉、某个依赖收紧了。锁文件
记录"上次装的是这组版本"，`ravel check` 负责在新的 registry 上重新核对：
锁住的版本还在不在、还能不能满足所有需求、有没有更新的版本被错过。

存成 JSON，键排序、缩进两格。这样 diff 里只有真正变化的那几行。
不做哈希、不做依赖摘要——这个项目要讲清楚的是"求解"，不是供应链安全。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .errors import LockError
from .registry import Registry, Requirement, parse_requirements
from .solver import Solution
from .versions import Version, format_constraint

LOCK_VERSION = 1
DEFAULT_PATH = "ravel.lock"


@dataclass
class Lock:
    """一份锁定的解。"""

    packages: Dict[str, str] = field(default_factory=dict)
    requirements: List[str] = field(default_factory=list)
    registry: str = ""

    def __len__(self) -> int:
        return len(self.packages)

    def items(self) -> List[Tuple[str, str]]:
        return sorted(self.packages.items(), key=lambda item: item[0].lower())

    def version_of(self, name: str) -> Optional[Version]:
        raw = self.packages.get(name)
        return Version.parse(raw) if raw is not None else None


@dataclass
class Check:
    """核对结果：`problems` 是错的，`notes` 只是提醒。"""

    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self) -> str:
        lines: List[str] = [f"  ✗ {item}" for item in self.problems]
        if not self.problems:
            lines.append("  ✓ 锁文件仍然成立。")
        lines.extend(f"  · {item}" for item in self.notes)
        return "\n".join(lines)


def from_solution(solution: Solution, registry: str = "") -> Lock:
    packages = {name: str(version) for name, version in solution.items()}
    requirements = [str(item) for item in solution.requirements]
    return Lock(packages, requirements, registry or solution.registry.source)


def to_json(lock: Lock) -> str:
    payload = {
        "lock_version": LOCK_VERSION,
        "registry": lock.registry,
        "requirements": lock.requirements,
        "packages": {name: lock.packages[name] for name, _ in lock.items()},
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def from_json(text: str, source: str = "<内存>") -> Lock:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LockError(f"{source} 不是合法 JSON：{exc.msg}（第 {exc.lineno} 行）") from None
    if not isinstance(payload, dict):
        raise LockError(f"{source} 的顶层应该是一个对象")
    version = payload.get("lock_version")
    if version != LOCK_VERSION:
        raise LockError(f"{source} 的 lock_version 是 {version!r}，这个版本只认 {LOCK_VERSION}")
    packages = payload.get("packages")
    if not isinstance(packages, dict):
        raise LockError(f"{source} 缺少 `packages` 字段")
    for name, raw in packages.items():
        if not isinstance(raw, str):
            raise LockError(f"{source} 里 {name} 的版本应该是字符串")
        Version.parse(raw)  # 早报错，别等到核对时才炸
    requirements = payload.get("requirements") or []
    if not isinstance(requirements, list) or not all(isinstance(i, str) for i in requirements):
        raise LockError(f"{source} 的 `requirements` 应该是字符串数组")
    return Lock(dict(packages), list(requirements), str(payload.get("registry", "")))


def load(path: str) -> Lock:
    if not os.path.exists(path):
        raise LockError(f"找不到锁文件：{path}")
    with open(path, encoding="utf-8") as handle:
        return from_json(handle.read(), source=path)


def save(lock: Lock, path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(to_json(lock))


def _requirements_text(requirements: Sequence[Requirement]) -> List[str]:
    return [str(item) for item in requirements]


def check(lock: Lock, registry: Registry,
          requirements: Optional[Sequence[Requirement]] = None) -> Check:
    """核对锁文件在新的 registry 上是否还站得住。"""
    result = Check()

    if requirements is not None:
        wanted = _requirements_text(requirements)
        if wanted != lock.requirements:
            result.problems.append("需求文件和锁文件对不上，需要重新生成锁文件")

    locked: Dict[str, Version] = {}
    for name, raw in lock.items():
        package = registry.get(name)
        if package is None:
            result.problems.append(f"registry 里已经没有 {name} 了")
            continue
        version = Version.parse(raw)
        if version not in package.versions:
            result.problems.append(f"{name} {version} 已经不在 registry 里")
            continue
        if package.release(version).yanked:
            result.problems.append(f"{name} {version} 已被撤回")
        locked[name.lower()] = version

    for name, version in lock.items():
        if name.lower() not in locked:
            continue
        package = registry.get(name)
        if package is None:  # pragma: no cover - 上面已经拦掉
            continue
        for dep_name, constraint in package.release(locked[name.lower()]).dependencies:
            got = locked.get(dep_name.lower())
            if got is None:
                result.problems.append(
                    f"{name} {version} 依赖 {dep_name} {format_constraint(constraint)}，"
                    f"但锁文件里没有 {dep_name}")
            elif not constraint.contains(got):
                result.problems.append(
                    f"{name} {version} 依赖 {dep_name} {format_constraint(constraint)}，"
                    f"锁文件里却是 {got}")

    if requirements is not None:
        for requirement in requirements:
            got = locked.get(requirement.name.lower())
            if got is None:
                result.problems.append(f"需求 {requirement} 在锁文件里没有对应版本")
            elif not requirement.constraint.contains(got):
                result.problems.append(
                    f"需求 {requirement} 与锁定的 {got} 不兼容")

    for name, raw in lock.items():
        package = registry.get(name)
        if package is None:
            continue
        version = Version.parse(raw)
        newer = [release.version for release in package.usable() if release.version > version]
        if newer:
            result.notes.append(
                f"{name} 有更新的版本 {newer[0]}（锁文件仍钉在 {version}）")
    return result


def load_requirements_text(lock: Lock) -> List[Requirement]:
    """从锁文件里记下的需求文本还原出需求对象。"""
    if not lock.requirements:
        return []
    return parse_requirements("\n".join(lock.requirements), source="锁文件")
