"""PubGrub 求解器。

主循环四步：

1. **传播**：对每条不相容，若它的项里只剩一个还不能判断，就把这个项取反记为推导。
2. **决策**：在"被需要但还没定版本"的包里挑候选最少的那个，取它最新的可用版本。
3. **冲突消解**：某条不相容的所有项都成立时，看最近一次被满足的项——
   若它是决策（或者回退到此它更早的层还能保住其余项），就回退换版本；
   否则把它自己的原因合并进来，得到一条更基础的不相容，继续。
4. **失败**：一路退到根仍然矛盾，把这棵推导树交给 :mod:`ravel.explain` 讲成人话。

三个容易写错的地方：

* **"还没有任何记录的包"必须算不确定，不能算被推翻。** 一个包的可行集合
  初始是"什么版本都行"，而"什么版本都行"和"这个包不许装"是不相交的，
  照集合运算会得出"已推翻"——那样连根需求都推不出"要用这个包"，整个
  传播根本不会启动。
* 回退的层级是"**其余项仍成立**的最大层级"，不是"上一个决策层"。否则回退
  之后冲突那条不相容不再是单元子句，刚试过的版本不会被排除，来回空转。
* 冲突消解合并原因时，要把原因里的项**全部**带进来（同名的会被构造器取交），
  少带一项就会学出一条过强的约束——拿暴力枚举对拍时才发现的。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .errors import SolveError
from .registry import Registry, Requirement
from .terms import (
    CONTRADICTED, INCONCLUSIVE, ROOT, Conflict, Dependency, Incompatibility,
    NoVersions, Term,
)
from .versions import Version, VersionRange

ROOT_VERSION = Version(0, 0, 0)
STEP_LIMIT = 100_000


@dataclass
class Stats:
    """一次求解里各步骤发生了多少次——用来证明回退真的发生了。"""

    decisions: int = 0
    derivations: int = 0
    backtracks: int = 0
    conflicts: int = 0
    incompatibilities: int = 0
    steps: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "decisions": self.decisions,
            "derivations": self.derivations,
            "backtracks": self.backtracks,
            "conflicts": self.conflicts,
            "incompatibilities": self.incompatibilities,
            "steps": self.steps,
        }


class SolveFailure(SolveError):
    """求解失败，带上最后那棵推导树。"""

    def __init__(self, incompatibility: Incompatibility, stats: Stats) -> None:
        super().__init__("依赖无法求解")
        self.incompatibility = incompatibility
        self.stats = stats


@dataclass
class Assignment:
    """部分解里的一条记录：要么是决策，要么是推导。"""

    term: Term
    decision_level: int
    cause: Optional[Incompatibility]
    index: int

    @property
    def is_decision(self) -> bool:
        return self.cause is None

    @property
    def name(self) -> str:
        return self.term.name

    def difference(self, term: Term) -> Optional[Term]:
        """本项里超出 `term` 的那部分——冲突消解时用来保留"多出来的限制"。"""
        return self.term.difference(term)


class PartialSolution:
    """当前已经确定（或被排除）的版本。"""

    def __init__(self) -> None:
        self.assignments: List[Assignment] = []
        self.decisions: Dict[str, Version] = {}
        self.decision_level = 0
        self._by_name: Dict[str, List[Assignment]] = {}
        self._cache: Dict[str, VersionRange] = {}

    # -- 写入 -------------------------------------------------------------
    def add_root(self, name: str, version: Version) -> Assignment:
        """根不算一层决策：层级 0，回退时永远保留。"""
        return self._register(Term(name, VersionRange.exact(version)), 0, None)

    def add_decision(self, name: str, version: Version) -> Assignment:
        self.decision_level += 1
        return self._register(Term(name, VersionRange.exact(version)),
                              self.decision_level, None)

    def derive(self, term: Term, cause: Incompatibility) -> Assignment:
        return self._register(term, self.decision_level, cause)

    def _register(self, term: Term, level: int, cause: Optional[Incompatibility]) -> Assignment:
        assignment = Assignment(term, level, cause, len(self.assignments))
        self.assignments.append(assignment)
        self._by_name.setdefault(term.name, []).append(assignment)
        self._cache.pop(term.name, None)
        if cause is None:
            version = term.allowed.exact_version
            if version is None:  # pragma: no cover - 决策一定是精确版本
                raise SolveError(f"决策必须是精确版本：{term.render()}")
            self.decisions[term.name] = version
        return assignment

    def backtrack(self, level: int) -> None:
        """丢掉层级高于 `level` 的所有记录。"""
        if level >= self.decision_level:
            return
        kept = [item for item in self.assignments if item.decision_level <= level]
        self.assignments = []
        self._by_name.clear()
        self._cache.clear()
        self.decisions.clear()
        self.decision_level = 0
        for assignment in kept:
            self.assignments.append(assignment)
            self._by_name.setdefault(assignment.name, []).append(assignment)
            if assignment.is_decision:
                version = assignment.term.allowed.exact_version
                if version is not None:
                    self.decisions[assignment.name] = version
                self.decision_level = max(self.decision_level, assignment.decision_level)

    # -- 查询 -------------------------------------------------------------
    def allowed(self, name: str) -> VersionRange:
        """当前解允许这个包取哪些版本。"""
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        allowed = VersionRange.any()
        for assignment in self._by_name.get(name, ()):  # type: ignore[union-attr]
            allowed = allowed.intersect(assignment.term.allowed)
            if allowed.is_empty:
                break
        self._cache[name] = allowed
        return allowed

    def assignments_for(self, name: str) -> Sequence[Assignment]:
        return tuple(self._by_name.get(name, ()))  # type: ignore[arg-type]

    def relation(self, term: Term) -> str:
        """这个项当前是成立、被推翻，还是说不准。"""
        if not self._by_name.get(term.name):
            # 还没有任何记录：只能说不确定
            return INCONCLUSIVE
        return term.relation_to(self.allowed(term.name))

    def satisfier(self, term: Term) -> Optional[Assignment]:
        """让 `term` 成立的那条最早记录。"""
        allowed = VersionRange.any()
        for assignment in self._by_name.get(term.name, ()):  # type: ignore[union-attr]
            allowed = allowed.intersect(assignment.term.allowed)
            if allowed.is_subset_of(term.allowed):
                return assignment
        return None


@dataclass
class Solution:
    """求解结果。"""

    registry: Registry
    requirements: Tuple[Requirement, ...]
    decisions: Dict[str, Version]
    stats: Stats = field(default_factory=Stats)

    def __contains__(self, name: str) -> bool:
        return name in self.decisions

    def __len__(self) -> int:
        return len(self.decisions)

    def version_of(self, name: str) -> Optional[Version]:
        return self.decisions.get(name)

    def items(self) -> List[Tuple[str, Version]]:
        return sorted(self.decisions.items(), key=lambda item: item[0].lower())

    def package_names(self) -> List[str]:
        return [name for name, _ in self.items()]

    def dependencies_of(self, name: str) -> List[Tuple[str, VersionRange]]:
        version = self.decisions.get(name)
        if version is None:
            return []
        package = self.registry.get(name)
        if package is None:  # pragma: no cover - 求解结果里的包一定存在
            return []
        return list(package.release(version).dependencies)

    def as_dict(self) -> Dict[str, str]:
        return {name: str(version) for name, version in self.items()}


class Solver:
    """把需求 + registry 变成一组确定的版本。"""

    def __init__(self, registry: Registry, requirements: Sequence[Requirement]) -> None:
        if not requirements:
            raise SolveError("没有任何需求")
        self.registry = registry
        self.requirements = tuple(requirements)
        self.stats = Stats()
        self.solution = PartialSolution()
        self._incompatibilities: Dict[str, List[Incompatibility]] = {}
        self._expanded: Set[Tuple[str, str]] = set()
        # 候选列表只跟"这个包当前被允许的区间"有关。区间对象在解法里是缓存复用
        # 的，所以拿它的身份当键就够了：区间一变，键就对不上，自然重算。
        self._candidate_cache: Dict[str, Tuple[VersionRange, List[Version]]] = {}

        self.solution.add_root(ROOT, ROOT_VERSION)
        root_term = Term(ROOT, VersionRange.exact(ROOT_VERSION))
        for requirement in self.requirements:
            self._add(Incompatibility(
                [root_term, Term(requirement.name, requirement.constraint).negate()],
                Dependency(ROOT, ROOT_VERSION, requirement),
            ))

    # -- 对外 -------------------------------------------------------------
    def solve(self) -> Solution:
        next_name = ROOT
        while True:
            conflict = self._propagate(next_name)
            if conflict is not None:
                self.stats.conflicts += 1
                next_name = self._resolve_conflict(conflict)
                continue

            name = self._pick_package()
            if name is None:
                decided = dict(self.solution.decisions)
                decided.pop(ROOT, None)
                return Solution(self.registry, self.requirements, decided, self.stats)

            version = self._choose_version(name)
            if version is None:
                self.stats.conflicts += 1
                next_name = self._report_no_versions(name)
                continue

            self._decide(name, version)
            next_name = name

    def _tick(self) -> None:
        self.stats.steps += 1
        if self.stats.steps > STEP_LIMIT:  # pragma: no cover - 防御性
            raise SolveError(f"求解步数超过 {STEP_LIMIT}，可能是算法里有环")

    # -- 传播 -------------------------------------------------------------
    def _propagate(self, name: str) -> Optional[Incompatibility]:
        queue = deque([name])
        while queue:
            current = queue.popleft()
            again = True
            while again:
                again = False
                for incompatibility in self._incompatibilities.get(current, ()):
                    kind, term = self._relation(incompatibility)
                    if kind == "conflict":
                        return incompatibility
                    if kind == "unit":
                        assert term is not None
                        self._tick()
                        self.solution.derive(term.negate(), incompatibility)
                        self.stats.derivations += 1
                        again = True
                        queue.append(term.name)
        return None

    def _relation(self, incompatibility: Incompatibility) -> Tuple[str, Optional[Term]]:
        inconclusive: Optional[Term] = None
        for term in incompatibility.terms:
            relation = self.solution.relation(term)
            if relation == CONTRADICTED:
                return "inactive", None
            if relation == INCONCLUSIVE:
                if inconclusive is not None:
                    return "inactive", None
                inconclusive = term
        if inconclusive is None:
            return "conflict", None
        return "unit", inconclusive

    # -- 决策 -------------------------------------------------------------
    def _pick_package(self) -> Optional[str]:
        pending: List[Tuple[int, str]] = []
        for name in self._required_names():
            if name == ROOT or name in self.solution.decisions:
                continue
            pending.append((len(self._candidates(name)), name))
        if not pending:
            return None
        pending.sort(key=lambda item: (item[0], item[1].lower()))
        return pending[0][1]

    def _required_names(self) -> Set[str]:
        names = {requirement.name for requirement in self.requirements}
        for name, version in self.solution.decisions.items():
            if name == ROOT:
                continue
            package = self.registry.get(name)
            if package is None:  # pragma: no cover - 决策的包一定来自 registry
                continue
            for dep_name, _ in package.release(version).dependencies:
                names.add(dep_name)
        return names

    def _candidates(self, name: str) -> List[Version]:
        package = self.registry.get(name)
        if package is None:
            return []
        allowed = self.solution.allowed(name)
        cached = self._candidate_cache.get(name)
        if cached is not None and cached[0] is allowed:
            return cached[1]
        found = [release.version for release in package.usable()
                 if allowed.contains(release.version)
                 and (not release.version.is_prerelease
                      or allowed.allows_prerelease(release.version))]
        self._candidate_cache[name] = (allowed, found)
        return found

    def _choose_version(self, name: str) -> Optional[Version]:
        candidates = self._candidates(name)
        return candidates[0] if candidates else None

    def _decide(self, name: str, version: Version) -> None:
        package = self.registry.require(name)
        release = package.release(version)
        self.solution.add_decision(name, version)
        self.stats.decisions += 1
        key = (name.lower(), str(version))
        if key in self._expanded:
            return
        self._expanded.add(key)
        for dep_name, constraint in release.dependencies:
            self._add(Incompatibility(
                [Term(name, VersionRange.exact(version)),
                 Term(dep_name, constraint).negate()],
                Dependency(name, version, Requirement(dep_name, constraint)),
            ))

    # -- 冲突消解 ---------------------------------------------------------
    def _report_no_versions(self, name: str) -> str:
        """区间里一个可用版本都没有：把"这个区间为空"记成一条不相容。

        只写"这个区间里没有版本"是不够的——它没有主语，消解到最后只剩一条空约束，
        于是"没有版本"这件事没人负责，一路退到根就误报成"需求之间互相矛盾"。
        必须把"是谁把它拉进来的"一起写进去：父包选了这个版本，才轮到它出错。
        """
        allowed = self.solution.allowed(name)
        known = name in self.registry
        terms = [Term(name, allowed)]
        terms.extend(self._support_terms(name))
        incompatibility = Incompatibility(
            terms, NoVersions(name, allowed, known=known))
        self._add(incompatibility)
        return self._resolve_conflict(incompatibility)

    def _support_terms(self, name: str) -> List[Term]:
        """把 `name` 拉进依赖图的那些约束里，父包那一侧的项。"""
        support: List[Term] = []
        seen: Set[str] = set()
        for incompatibility in self._incompatibilities.get(name, ()):
            if not isinstance(incompatibility.cause, Dependency):
                continue
            for term in incompatibility.terms:
                if term.name == name or term.name in seen:
                    continue
                if self.solution.satisfier(term) is None:
                    continue
                seen.add(term.name)
                support.append(term)
        return support

    def _resolve_conflict(self, incompatibility: Incompatibility) -> str:
        while True:
            self._tick()

            # 已经没有任何项：根需求本身就无法满足
            if not incompatibility.terms:
                raise SolveFailure(incompatibility, self.stats)

            found: List[Tuple[Term, Assignment]] = []
            for term in incompatibility.terms:
                satisfier = self.solution.satisfier(term)
                if satisfier is not None:
                    found.append((term, satisfier))
            if not found:
                raise SolveFailure(incompatibility, self.stats)
            if len(found) != len(incompatibility.terms):
                # 有些项已经不成立了——这条约束是空话，丢掉那些项再看
                incompatibility = Incompatibility(
                    [term for term, _ in found], incompatibility.cause)
                continue

            term, most_recent = found[0]
            previous_level = 0
            difference = most_recent.difference(term)
            if difference is not None:
                prior = self.solution.satisfier(difference.negate())
                if prior is not None:
                    previous_level = max(previous_level, prior.decision_level)

            for other_term, satisfier in found[1:]:
                if satisfier.index > most_recent.index:
                    previous_level = max(previous_level, most_recent.decision_level)
                    term, most_recent = other_term, satisfier
                    difference = satisfier.difference(other_term)
                    if difference is not None:
                        prior = self.solution.satisfier(difference.negate())
                        if prior is not None:
                            previous_level = max(previous_level, prior.decision_level)
                else:
                    previous_level = max(previous_level, satisfier.decision_level)

            # 最近这一项是猜出来的：退回去，换下一个候选版本
            if most_recent.is_decision or previous_level < most_recent.decision_level:
                if most_recent.name == ROOT:
                    raise SolveFailure(incompatibility, self.stats)
                level = min(previous_level, most_recent.decision_level - 1)
                self.solution.backtrack(level)
                self.stats.backtracks += 1
                return most_recent.name

            # 最近这一项也是推出来的：把它的原因并进来，得到更基础的矛盾
            cause = most_recent.cause
            if cause is None:  # pragma: no cover - 决策在上面已经处理
                raise SolveFailure(incompatibility, self.stats)
            terms = [item for item in incompatibility.terms if item is not term]
            # 原因里"同一个包"的那一项正是这条推导取反的对象，它必然已经被推翻；
            # 把它带进来下一轮就会被当成"不成立"丢掉，学出的约束会强到无条件成立。
            terms.extend(item for item in cause.terms if item.name != most_recent.name)
            if difference is not None:
                terms.append(difference.negate())
            incompatibility = Incompatibility(terms, Conflict(incompatibility, cause))
            self._add(incompatibility)

    # -- 记账 -------------------------------------------------------------
    def _add(self, incompatibility: Incompatibility) -> None:
        self.stats.incompatibilities += 1
        for term in incompatibility.terms:
            self._incompatibilities.setdefault(term.name, []).append(incompatibility)


def resolve(registry: Registry, requirements: Sequence[Requirement]) -> Solution:
    """便捷入口：求解或者抛 :class:`SolveFailure`。"""
    return Solver(registry, requirements).solve()
