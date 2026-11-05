"""registry 与需求文件。

registry 就是"有哪些包、每个包有哪些版本、每个版本依赖谁"。存成 JSON，
加载时把所有约束一次性解析成 :class:`~ravel.versions.VersionRange`，
并把版本按从新到旧排好——求解器每次挑版本都从列表头部开始。

需求文件是 requirements.txt 那种朴素格式：一行一个 `包名 约束`，`#` 注释。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .errors import RegistryError
from .versions import Version, VersionRange, format_constraint, parse_constraint

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class Requirement:
    """一条"需要某个包的某个版本区间"。

    `text` 保留需求文件里的原始写法（比如 `^1.2`），显示时优先用它——
    报错信息里出现用户自己写的字符串，比出现规范化之后的区间好认。
    """

    name: str
    constraint: VersionRange
    line: int = 0
    text: str = ""

    def __str__(self) -> str:
        if self.text:
            return f"{self.name} {self.text}"
        if self.constraint.is_any:
            return self.name
        return f"{self.name} {format_constraint(self.constraint)}"


@dataclass(frozen=True)
class Release:
    """某个包的一个具体版本，以及它带出来的依赖。"""

    version: Version
    dependencies: Tuple[Tuple[str, VersionRange], ...] = ()
    yanked: bool = False

    def dependency_names(self) -> List[str]:
        return [name for name, _ in self.dependencies]

    def dependency_on(self, name: str) -> Optional[VersionRange]:
        for dep_name, constraint in self.dependencies:
            if dep_name == name:
                return constraint
        return None


class Package:
    """一个包的所有版本，按从新到旧排列。"""

    __slots__ = ("name", "releases", "description", "_index")

    def __init__(self, name: str, releases: Iterable[Release],
                 description: str = "") -> None:
        self.name = name
        self.description = description
        ordered = sorted(releases, key=lambda release: release.version, reverse=True)
        seen: Dict[Version, Release] = {}
        for release in ordered:
            if release.version in seen:
                raise RegistryError(f"{name} 的版本 {release.version} 写了不止一次")
            seen[release.version] = release
        self.releases: Tuple[Release, ...] = tuple(ordered)
        self._index: Dict[Version, Release] = seen

    def __len__(self) -> int:
        return len(self.releases)

    @property
    def versions(self) -> Tuple[Version, ...]:
        return tuple(release.version for release in self.releases)

    def release(self, version: Version) -> Release:
        try:
            return self._index[version]
        except KeyError:
            raise RegistryError(f"{self.name} 没有版本 {version}") from None

    def usable(self) -> Tuple[Release, ...]:
        """跳过被撤回（yanked）的版本。"""
        return tuple(release for release in self.releases if not release.yanked)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Package {self.name} {len(self.releases)} 个版本>"


class Registry:
    """一组包。名字大小写不敏感，但保留最初写法的显示形式。"""

    def __init__(self, packages: Iterable[Package], source: str = "内置") -> None:
        self.source = source
        self._packages: Dict[str, Package] = {}
        self._display: Dict[str, str] = {}
        for package in packages:
            key = package.name.lower()
            if key in self._packages:
                raise RegistryError(f"包名重复：{package.name}")
            self._packages[key] = package
            self._display[key] = package.name

    # -- 查询 -------------------------------------------------------------
    def __contains__(self, name: str) -> bool:
        return name.lower() in self._packages

    def __len__(self) -> int:
        return len(self._packages)

    def names(self) -> List[str]:
        return sorted(self._display.values(), key=str.lower)

    def get(self, name: str) -> Optional[Package]:
        return self._packages.get(name.lower())

    def require(self, name: str) -> Package:
        package = self.get(name)
        if package is None:
            raise RegistryError(f"registry 里没有这个包：{name}")
        return package

    @property
    def release_count(self) -> int:
        return sum(len(package) for package in self._packages.values())

    def stats(self) -> Tuple[int, int]:
        return len(self), self.release_count

    # -- 加载 -------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict, source: str = "<内存>") -> "Registry":
        if not isinstance(data, dict) or "packages" not in data:
            raise RegistryError("registry 需要顶层 `packages` 字段")
        raw_packages = data["packages"]
        if not isinstance(raw_packages, dict):
            raise RegistryError("`packages` 应该是一个对象：包名 → 包定义")
        packages = []
        for name, body in raw_packages.items():
            packages.append(_package_from_dict(name, body))
        return cls(packages, source=source)

    @classmethod
    def from_json_text(cls, text: str, source: str = "<内存>") -> "Registry":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RegistryError(f"registry 不是合法 JSON：{exc.msg}（第 {exc.lineno} 行）") from None
        return cls.from_dict(data, source=source)

    @classmethod
    def load(cls, path: str) -> "Registry":
        if not os.path.exists(path):
            raise RegistryError(f"找不到 registry 文件：{path}")
        with open(path, encoding="utf-8") as handle:
            return cls.from_json_text(handle.read(), source=path)


def _package_from_dict(name: str, body: dict) -> Package:
    if not _NAME_RE.match(name):
        raise RegistryError(f"包名不合法：{name!r}")
    if not isinstance(body, dict):
        raise RegistryError(f"{name} 的定义应该是一个对象")
    raw_releases = body.get("releases")
    if not isinstance(raw_releases, list) or not raw_releases:
        raise RegistryError(f"{name} 至少需要一个版本")
    releases = []
    for item in raw_releases:
        releases.append(_release_from_dict(name, item))
    return Package(name, releases, description=str(body.get("description", "")))


def _release_from_dict(package: str, item: dict) -> Release:
    if not isinstance(item, dict) or "version" not in item:
        raise RegistryError(f"{package} 的版本条目缺少 `version`")
    version = Version.parse(str(item["version"]))
    raw_deps = item.get("dependencies")
    if raw_deps is None:
        raw_deps = {}
    if not isinstance(raw_deps, dict):
        raise RegistryError(f"{package} {version} 的 `dependencies` 应该是对象")
    dependencies = []
    for dep_name, constraint in raw_deps.items():
        if not _NAME_RE.match(dep_name):
            raise RegistryError(f"{package} {version} 依赖了不合法的包名：{dep_name!r}")
        try:
            parsed = parse_constraint(str(constraint))
        except Exception as exc:  # noqa: BLE001 - 统一转成 registry 错误
            message = getattr(exc, "message", str(exc))
            raise RegistryError(
                f"{package} {version} 对 {dep_name} 的约束看不懂：{message}") from None
        dependencies.append((dep_name, parsed))
    yanked = bool(item.get("yanked", False))
    return Release(version, tuple(dependencies), yanked)


def parse_requirements(text: str, source: str = "<内存>") -> List[Requirement]:
    """解析 requirements.txt 风格的需求。"""
    requirements: List[Requirement] = []
    seen: Dict[str, int] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, rest = _split_requirement(line, source, number)
        key = name.lower()
        if key in seen:
            raise RegistryError(
                f"{source} 第 {number} 行：{name} 已经在前面的第 {seen[key]} 行出现过")
        seen[key] = number
        try:
            constraint = parse_constraint(rest)
        except Exception as exc:  # noqa: BLE001
            message = getattr(exc, "message", str(exc))
            raise RegistryError(f"{source} 第 {number} 行：{message}") from None
        requirements.append(Requirement(name, constraint, number, rest))
    if not requirements:
        raise RegistryError(f"{source} 里没有任何需求")
    return requirements


def _split_requirement(line: str, source: str, number: int) -> Tuple[str, str]:
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*)$", line)
    if match is None:
        raise RegistryError(f"{source} 第 {number} 行：看不懂这一行 {line!r}")
    name, rest = match.group(1), match.group(2).strip()
    if rest.startswith("=") and not rest.startswith("=="):
        rest = "=" + rest[1:].strip()
    return name, rest


def load_requirements(path: str) -> List[Requirement]:
    if not os.path.exists(path):
        raise RegistryError(f"找不到需求文件：{path}")
    with open(path, encoding="utf-8") as handle:
        return parse_requirements(handle.read(), source=path)


def dump_requirements(requirements: Sequence[Requirement]) -> str:
    lines = [str(item) for item in requirements]
    return "\n".join(lines) + "\n"
