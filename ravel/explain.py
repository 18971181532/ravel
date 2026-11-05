"""把推导树翻译成人话。

求解失败时，PubGrub 手里有的不是一句"无解"，而是一棵完整的推导树：每一条
不相容要么来自外部（谁依赖谁、哪个区间里没有版本），要么由两条不相容合起来
得到。这个模块做的事只有一件——把这棵树按"先因后果"的顺序摊平，每条写一句
人话，后一条用编号引用前一条。

于是"装不上"从一个死胡同变成了一段可以读的论证：

    1. 项目要求 http ^1.0
    2. web 2.0.0 依赖 http ^2.0
    3. 由 1、2 可得：web 2.0.0 不能成立
    ...
    9. 由 7、8 可得：没有任何一组版本能满足全部需求

最后再给一条能落地的建议：把哪条需求放宽就能装上。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .registry import Registry, Requirement
from .solver import ROOT, SolveFailure, Solver, Stats
from .terms import Conflict, Dependency, Incompatibility, NoVersions, Term
from .versions import VersionRange

MAX_SUGGESTIONS = 3

#: 一句话结论。步骤里的最后一条和文末的「结论」用同一个说法，读着才不别扭。
NO_SOLUTION = "没有任何一组版本能同时满足全部需求"


@dataclass(frozen=True)
class Step:
    """推导过程里的一行。"""

    number: int
    body: str
    parents: Tuple[int, ...] = ()

    @property
    def is_derived(self) -> bool:
        return bool(self.parents)


@dataclass
class Explanation:
    """一次失败的完整解释。"""

    failure: Incompatibility
    steps: List[Step]
    suggestions: List[str] = field(default_factory=list)
    root_requirements: List[Requirement] = field(default_factory=list)
    stats: Optional[Stats] = None

    # -- 渲染 -------------------------------------------------------------
    def render(self) -> str:
        lines: List[str] = []
        lines.append("无解。一步步推导如下：")
        lines.append("")
        width = len(str(len(self.steps)))
        for step in self.steps:
            lines.append(f"  {step.number:>{width}}. {step.body}")
        lines.append("")
        lines.append("结论：" + NO_SOLUTION + "。")
        if self.suggestions:
            lines.append("")
            lines.append("可以试试：")
            for item in self.suggestions:
                lines.append(f"  · {item}")
        return "\n".join(lines)

    def tree(self) -> str:
        """同一棵树换个画法：从结论往下展开，末梢是事实。

        线性列表适合"按顺序读"，树适合"看清楚哪两条撞在一起"。
        """
        lines = ["无解"]
        walk(self.failure, "", lines)
        return "\n".join(lines)

    def headline(self) -> str:
        """一句话版本，给命令行摘要用。"""
        names = [str(item) for item in self.root_requirements]
        if names:
            return "无解：" + "、".join(names) + " 之间没有共同解"
        return "无解"


def _label(incompatibility: Incompatibility) -> str:
    """叶子写事实，中间节点写结论。"""
    return (_clause(incompatibility)
            if isinstance(incompatibility.cause, Conflict)
            else _body(incompatibility, ()))


def walk(incompatibility: Incompatibility, prefix: str, lines: List[str]) -> None:
    cause = incompatibility.cause
    if not isinstance(cause, Conflict):
        return
    children = [cause.left, cause.right]
    for index, child in enumerate(children):
        last = index == len(children) - 1
        lines.append(prefix + ("└─ " if last else "├─ ") + _label(child))
        walk(child, prefix + ("   " if last else "│  "), lines)


def _clause(incompatibility: Incompatibility) -> str:
    """把一条不相容写成一句话，顺手把内部占位的根包名藏掉。"""
    terms = [term for term in incompatibility.terms if term.name != ROOT]
    if not terms:
        return NO_SOLUTION
    return "、".join(term.render() for term in terms) + " 不能同时成立"


def _no_versions_body(cause: NoVersions) -> str:
    if not cause.known:
        return f"registry 里根本没有 {cause.package} 这个包"
    if cause.allowed.is_empty:
        return f"{cause.package} 的约束加起来一个版本都不剩"
    if cause.allowed.is_any:
        return f"{cause.package} 一个可用版本都没有"
    return f"{cause.package} 在 {cause.allowed.render()} 里没有可用版本"


def _body(incompatibility: Incompatibility, parents: Tuple[int, ...]) -> str:
    cause = incompatibility.cause
    if isinstance(cause, Dependency):
        if cause.package == ROOT:
            return f"项目要求 {cause.requirement}"
        return f"{cause.package} {cause.version} 依赖 {cause.requirement}"
    if isinstance(cause, NoVersions):
        return _no_versions_body(cause)
    if isinstance(cause, Conflict):
        joined = "、".join(str(number) for number in parents)
        return f"由 {joined} 可得：{_clause(incompatibility)}"
    return _clause(incompatibility)


def derivation(failure: Incompatibility) -> List[Step]:
    """把推导树摊平成按因果排序的步骤；同一个节点只讲一次。"""
    order: List[Incompatibility] = []
    numbers: Dict[int, int] = {}
    parents_of: Dict[int, Tuple[int, ...]] = {}

    def visit(incompatibility: Incompatibility) -> int:
        key = id(incompatibility)
        known = numbers.get(key)
        if known is not None:
            return known
        cause = incompatibility.cause
        parents: Tuple[int, ...] = ()
        if isinstance(cause, Conflict):
            parents = (visit(cause.left), visit(cause.right))
        numbers[key] = len(order) + 1
        order.append(incompatibility)
        parents_of[key] = parents
        return numbers[key]

    visit(failure)

    # 先摆事实、再摆推理：读的时候不用来回跳。稳定排序不改变同类内部的先后，
    # 所以每一条引用的编号依然排在它前面；排完再统一重新编号。
    ordered = sorted(enumerate(order, start=1),
                     key=lambda pair: isinstance(pair[1].cause, Conflict))
    renumber = {old: new for new, (old, _) in enumerate(ordered, start=1)}

    steps: List[Step] = []
    for new_number, (_, incompatibility) in enumerate(ordered, start=1):
        parents = tuple(renumber[item] for item in parents_of[id(incompatibility)])
        steps.append(Step(new_number, _body(incompatibility, parents), parents))
    return steps


def _root_requirements(failure: Incompatibility) -> List[Requirement]:
    """从推导树里挑出用到的根需求，按出现顺序去重。"""
    found: List[Requirement] = []
    seen: set = set()

    def visit(incompatibility: Incompatibility) -> None:
        if id(incompatibility) in seen:
            return
        seen.add(id(incompatibility))
        cause = incompatibility.cause
        if isinstance(cause, Conflict):
            visit(cause.left)
            visit(cause.right)
        elif isinstance(cause, Dependency) and cause.package == ROOT:
            if cause.requirement not in found:
                found.append(cause.requirement)

    visit(failure)
    return found


def _missing_packages(failure: Incompatibility) -> List[str]:
    """推导里出现过的"registry 里压根没有"的包。"""
    found: List[str] = []
    seen: set = set()

    def visit(incompatibility: Incompatibility) -> None:
        if id(incompatibility) in seen:
            return
        seen.add(id(incompatibility))
        cause = incompatibility.cause
        if isinstance(cause, Conflict):
            visit(cause.left)
            visit(cause.right)
        elif isinstance(cause, NoVersions) and not cause.known:
            if cause.package not in found:
                found.append(cause.package)

    visit(failure)
    return found


def _suggestions(registry: Optional[Registry],
                 requirements: Sequence[Requirement],
                 failure: Optional[Incompatibility] = None) -> List[str]:
    """试着重放每一条需求：放宽或去掉哪一条就能装上。"""
    out: List[str] = []
    if failure is not None:
        for name in _missing_packages(failure):
            out.append(f"registry 里还没有 {name}，先把它的版本补进去")
    if registry is None or not requirements:
        return out
    for requirement in requirements:
        if len(requirements) > 1:
            rest = [item for item in requirements if item is not requirement]
            try:
                Solver(registry, rest).solve()
            except SolveFailure:
                pass
            else:
                out.append(f"去掉需求 `{requirement}` 就能装上")
                if len(out) >= MAX_SUGGESTIONS:
                    return out
        relaxed = [
            Requirement(item.name, VersionRange.any(), item.line)
            if item is requirement else item
            for item in requirements
        ]
        try:
            solution = Solver(registry, relaxed).solve()
        except SolveFailure:
            continue
        picked = solution.version_of(requirement.name)
        tail = f"，会选 {requirement.name} {picked}" if picked is not None else ""
        out.append(f"把 `{requirement}` 放宽成 `{requirement.name}` 就能装上{tail}")
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


def explain(failure: SolveFailure,
            registry: Optional[Registry] = None,
            requirements: Sequence[Requirement] = ()) -> Explanation:
    """把 :class:`~ravel.solver.SolveFailure` 变成一份可读的解释。"""
    return Explanation(
        failure=failure.incompatibility,
        steps=derivation(failure.incompatibility),
        suggestions=_suggestions(registry, requirements, failure.incompatibility)[:MAX_SUGGESTIONS],
        root_requirements=_root_requirements(failure.incompatibility),
        stats=failure.stats,
    )


def explain_or_fail(registry: Registry,
                    requirements: Sequence[Requirement]) -> Explanation:
    """直接对着需求求解；有解就报错（这是给"本该装不上"的场合用的）。"""
    try:
        Solver(registry, requirements).solve()
    except SolveFailure as failure:
        return explain(failure, registry, requirements)
    raise ValueError("这组需求其实是有解的，没什么可解释的")
