"""PubGrub 的两个基本对象：Term（项）与 Incompatibility（不相容）。

**Term** 是"某个包的版本必须落在某个集合里"这样一条断言。写法上分正反：
`Term(name, ^2.0)` 表示"包的版本在 ^2.0 里"，取反就是"包的版本不在 ^2.0 里"。
内部统一存成"允许的版本集合"（:class:`~ravel.versions.VersionRange`），
于是判断两个项是否矛盾就退化成判断两个集合是否相交。

**Incompatibility** 是"这些项不可能同时成立"一条约束。它的来源有两种：

* 外部原因：某个版本依赖某个区间（:class:`Dependency`），或者某个包根本没有可用版本（:class:`NoVersions`）；
* 推导原因：两条不相容冲突消解出来的新约束（:class:`Conflict`）。

推导原因是一棵树——这正是"为什么装不上"能一句句讲出来的原因。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .registry import Requirement
from .versions import Version, VersionRange, format_constraint

#: 根的占位包名。它只活在求解器内部，不该出现在任何给人看的文字里。
ROOT = "__root__"

SATISFIED = "satisfied"
CONTRADICTED = "contradicted"
INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class Term:
    """一个包 + 一个允许的版本集合。"""

    name: str
    allowed: VersionRange

    def negate(self) -> "Term":
        return Term(self.name, self.allowed.complement())

    def relation_to(self, allowed: VersionRange) -> str:
        """当前可行集合 `allowed` 与这个项的关系。"""
        if allowed.is_subset_of(self.allowed):
            return SATISFIED
        if allowed.is_disjoint(self.allowed):
            return CONTRADICTED
        return INCONCLUSIVE

    def difference(self, other: "Term") -> Optional["Term"]:
        """本项里有、而 `other` 里没有的那部分；没有剩余就返回 None。"""
        rest = self.allowed.difference(other.allowed)
        if rest.is_empty:
            return None
        return Term(other.name, rest)

    def render(self) -> str:
        """写成一句人话。

        取反之后的项往往是"两段尾巴夹一个洞"，直接念出来是
        `>=2.0.0, <3.0.0 之外` 这种绕口的写法；补集比自身简单时反过来用
        "不在……里" 表述，读起来才像话。
        """
        if self.allowed.is_empty:
            return f"没有 {self.name}"
        if self.allowed.is_any:
            return f"{self.name} 的任何版本"
        exact = self.allowed.exact_version
        if exact is not None:
            return f"{self.name} {exact}"
        inner = self.allowed.complement()
        if inner.exact_version is not None:
            return f"{self.name} 不是 {inner.exact_version}"
        if len(self.allowed.intervals) > len(inner.intervals):
            text = format_constraint(inner)
            if len(inner.intervals) > 1:
                # 两个以上区间时加括号，"不在 2.0.0 或 2.1.0 里"会读岔
                return f"{self.name} 不在（{text}）里"
            return f"{self.name} 不在 {text} 里"
        return f"{self.name} 在 {format_constraint(self.allowed)} 里"

    def __str__(self) -> str:
        return self.render()


# ---------------------------------------------------------------------------
# 不相容的来源
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Dependency:
    """外部原因：`package version` 依赖 `requirement`。"""

    package: str
    version: Version
    requirement: Requirement


@dataclass(frozen=True)
class NoVersions:
    """外部原因：包在某个区间里没有可用版本。"""

    package: str
    allowed: VersionRange
    known: bool = True  # False 表示 registry 里压根没有这个包


@dataclass(frozen=True)
class Conflict:
    """推导原因：两条不相容合起来导致的新约束。"""

    left: "Incompatibility"
    right: "Incompatibility"


Cause = object  # Dependency | NoVersions | Conflict


class Incompatibility:
    """一组不可能同时成立的项。"""

    __slots__ = ("terms", "cause", "sequence")

    _counter = 0

    def __init__(self, terms: Sequence[Term], cause: Optional[Cause] = None) -> None:
        # 同一个包只保留一个项；构造时就去重，避免消解时出现重复项
        merged: List[Term] = []
        index = {}
        for term in terms:
            if term.name in index:
                position = index[term.name]
                merged[position] = Term(term.name, merged[position].allowed.intersect(term.allowed))
            else:
                index[term.name] = len(merged)
                merged.append(term)
        self.terms: Tuple[Term, ...] = tuple(merged)
        self.cause = cause
        Incompatibility._counter += 1
        self.sequence = Incompatibility._counter

    def __len__(self) -> int:
        return len(self.terms)

    def term_for(self, name: str) -> Optional[Term]:
        for term in self.terms:
            if term.name == name:
                return term
        return None

    def is_failure(self) -> bool:
        """只剩一个项、且这一项必然成立——说明根需求本身就无法满足。"""
        return not self.terms

    def render(self) -> str:
        visible = [term for term in self.terms if term.name != ROOT]
        if not visible:
            return "需求之间互相矛盾"
        return "、".join(term.render() for term in visible) + " 不能同时成立"

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Incompatibility {self.render()}>"
