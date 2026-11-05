"""版本与版本区间。

两层东西：

* :class:`Version` —— 严格的 SemVer 2.0 版本号（预发布标识参与比较，构建元数据不参与）。
* :class:`VersionRange` —— 若干互不相交的区间的并集。依赖约束解析成它之后，
  求解器只需要做纯粹的集合运算（交、并、补），不必再关心 `^` / `~` 这些语法糖。

区间一律规范化为"排序、互不重叠、能合并的已合并"，所以两个区间对象相等
就等于它们表示的版本集合相等——求解器里到处依赖这一点。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Optional, Sequence, Tuple, Union

from .errors import VersionError

_NUMERIC = re.compile(r"^\d+$")
_VERSION_RE = re.compile(
    r"^[vV]?"
    r"(\d+)(?:\.(\d+))?(?:\.(\d+))?"
    r"(?:-([0-9A-Za-z.-]+))?"
    r"(?:\+([0-9A-Za-z.-]+))?$"
)


# ---------------------------------------------------------------------------
# 版本号
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Version:
    """一个 SemVer 版本。排序即语义版本优先级。"""

    major: int
    minor: int = 0
    patch: int = 0
    prerelease: Tuple[Union[int, str], ...] = ()
    # 构建元数据不参与优先级比较，也不参与相等判断（SemVer 2.0 第 10 条）
    build: str = field(default="", compare=False)

    # -- 解析 -------------------------------------------------------------
    @classmethod
    def parse(cls, text: str) -> "Version":
        raw = text.strip()
        match = _VERSION_RE.match(raw)
        if match is None:
            raise VersionError(f"不是合法的版本号：{text!r}（期望 1、1.2 或 1.2.3）")
        major, minor, patch, pre, build = match.groups()
        identifiers: Tuple[Union[int, str], ...] = ()
        if pre:
            parts = []
            for piece in pre.split("."):
                if not piece:
                    raise VersionError(f"版本号里的预发布标识不能为空：{text!r}")
                if _NUMERIC.match(piece):
                    parts.append(int(piece))
                else:
                    parts.append(piece)
            identifiers = tuple(parts)
        return cls(int(major), int(minor or 0), int(patch or 0), identifiers, build or "")

    @classmethod
    def coerce(cls, value: Union[str, "Version", int]) -> "Version":
        if isinstance(value, Version):
            return value
        if isinstance(value, int):
            return cls(value)
        return cls.parse(value)

    # -- 排序 -------------------------------------------------------------
    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    def _key(self):
        if not self.prerelease:
            # 有预发布标识的版本小于同号正式版：用 1 / 0 打头区分
            return (self.major, self.minor, self.patch, 1, ())
        identifiers = []
        for piece in self.prerelease:
            if isinstance(piece, int):
                identifiers.append((0, piece, ""))
            else:
                identifiers.append((1, 0, piece))
        return (self.major, self.minor, self.patch, 0, tuple(identifiers))

    def __lt__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __le__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() <= other._key()

    def __gt__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() > other._key()

    def __ge__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() >= other._key()

    # -- 渲染 -------------------------------------------------------------
    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(str(piece) for piece in self.prerelease)
        if self.build:
            text += "+" + self.build
        return text

    @property
    def release(self) -> Tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def bump_major(self) -> "Version":
        return Version(self.major + 1, 0, 0)

    def bump_minor(self) -> "Version":
        return Version(self.major, self.minor + 1, 0)

    def bump_patch(self) -> "Version":
        return Version(self.major, self.minor, self.patch + 1)


# ---------------------------------------------------------------------------
# 区间
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Interval:
    """一个连续区间；`None` 表示那一侧没有边界。"""

    lower: Optional[Version] = None
    lower_inclusive: bool = True
    upper: Optional[Version] = None
    upper_inclusive: bool = False

    def contains(self, version: Version) -> bool:
        if self.lower is not None:
            if version < self.lower:
                return False
            if version == self.lower and not self.lower_inclusive:
                return False
        if self.upper is not None:
            if version > self.upper:
                return False
            if version == self.upper and not self.upper_inclusive:
                return False
        return True

    @property
    def is_empty(self) -> bool:
        if self.lower is None or self.upper is None:
            return False
        if self.lower < self.upper:
            return False
        if self.lower == self.upper:
            return not (self.lower_inclusive and self.upper_inclusive)
        return True

    @property
    def is_exact(self) -> bool:
        return (self.lower is not None and self.lower == self.upper
                and self.lower_inclusive and self.upper_inclusive)

    def render(self) -> str:
        if self.lower is None and self.upper is None:
            return "*"
        if self.is_exact:
            return str(self.lower)
        if self.lower is None:
            return ("<=" if self.upper_inclusive else "<") + str(self.upper)
        if self.upper is None:
            return (">=" if self.lower_inclusive else ">") + str(self.lower)
        left = (">=" if self.lower_inclusive else ">") + str(self.lower)
        right = ("<=" if self.upper_inclusive else "<") + str(self.upper)
        return f"{left}, {right}"

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return self.render()


def _compare_lower(left: Interval, right: Interval) -> int:
    """按"谁的下界更靠下"排序，下界相同则含边界者在前。"""
    if left.lower is None:
        return 0 if right.lower is None else -1
    if right.lower is None:
        return 1
    if left.lower != right.lower:
        return -1 if left.lower < right.lower else 1
    if left.lower_inclusive == right.lower_inclusive:
        return 0
    return -1 if left.lower_inclusive else 1


def _touches(left: Interval, right: Interval) -> bool:
    """左区间是否与右区间重叠或首尾相接。"""
    if left.upper is None or right.lower is None:
        return True
    if right.lower < left.upper:
        return True
    if right.lower == left.upper:
        return left.upper_inclusive or right.lower_inclusive
    return False


def _merge(left: Interval, right: Interval) -> Interval:
    assert _touches(left, right)
    if left.upper is None or right.upper is None:
        upper, upper_inclusive = None, False
    elif right.upper > left.upper:
        upper, upper_inclusive = right.upper, right.upper_inclusive
    elif right.upper < left.upper:
        upper, upper_inclusive = left.upper, left.upper_inclusive
    else:
        upper, upper_inclusive = left.upper, left.upper_inclusive or right.upper_inclusive
    return Interval(left.lower, left.lower_inclusive, upper, upper_inclusive)


@dataclass(frozen=True)
class VersionRange:
    """若干区间的并集，构造时自动规范化。"""

    intervals: Tuple[Interval, ...] = ()

    # -- 构造 -------------------------------------------------------------
    @classmethod
    def build(cls, intervals: Iterable[Interval]) -> "VersionRange":
        kept = [item for item in intervals if not item.is_empty]
        kept.sort(key=_sort_key)
        merged: list[Interval] = []
        for item in kept:
            if merged and _touches(merged[-1], item):
                merged[-1] = _merge(merged[-1], item)
            else:
                merged.append(item)
        return cls(tuple(merged))

    @classmethod
    def any(cls) -> "VersionRange":
        return ANY

    @classmethod
    def nothing(cls) -> "VersionRange":
        return NOTHING

    @classmethod
    def exact(cls, version: Version) -> "VersionRange":
        return cls((Interval(version, True, version, True),))

    @classmethod
    def at_least(cls, version: Version, inclusive: bool = True) -> "VersionRange":
        return cls((Interval(version, inclusive, None, False),))

    @classmethod
    def at_most(cls, version: Version, inclusive: bool = True) -> "VersionRange":
        return cls((Interval(None, True, version, inclusive),))

    # -- 查询 -------------------------------------------------------------
    def contains(self, version: Version) -> bool:
        for interval in self.intervals:
            if interval.contains(version):
                return True
        return False

    @property
    def is_empty(self) -> bool:
        return not self.intervals

    @property
    def is_any(self) -> bool:
        return (len(self.intervals) == 1
                and self.intervals[0].lower is None
                and self.intervals[0].upper is None)

    @property
    def exact_version(self) -> Optional[Version]:
        if len(self.intervals) == 1 and self.intervals[0].is_exact:
            return self.intervals[0].lower
        return None

    def allows_prerelease(self, version: Version) -> bool:
        """这条约束有没有"点名"这个预发布系列。

        预发布版本按 SemVer 排在同号正式版之前，集合运算会把它算进去——但没人
        会在 `^2.0` 里期待一个 rc。所以再加一道：预发布版本只有被约束点名时才
        参与选择，点名的形式是"下界就是同一组 major.minor.patch 的预发布"。
        这和 npm 的规矩一致：想装 rc，得自己写上。
        """
        for interval in self.intervals:
            lower = interval.lower
            if lower is not None and lower.prerelease and lower.release == version.release:
                return True
        return False

    # -- 集合运算 ---------------------------------------------------------
    def intersect(self, other: "VersionRange") -> "VersionRange":
        if self is other:
            return self
        return _intersect(self, other)

    def _intersect_uncached(self, other: "VersionRange") -> "VersionRange":
        pieces: list[Interval] = []
        for left in self.intervals:
            for right in other.intervals:
                lower, lower_inc = left.lower, left.lower_inclusive
                if right.lower is not None:
                    if lower is None or right.lower > lower:
                        lower, lower_inc = right.lower, right.lower_inclusive
                    elif right.lower == lower:
                        lower_inc = lower_inc and right.lower_inclusive

                upper, upper_inc = left.upper, left.upper_inclusive
                if right.upper is not None:
                    if upper is None or right.upper < upper:
                        upper, upper_inc = right.upper, right.upper_inclusive
                    elif right.upper == upper:
                        upper_inc = upper_inc and right.upper_inclusive

                piece = Interval(lower, lower_inc, upper, upper_inc)
                if not piece.is_empty:
                    pieces.append(piece)
        return VersionRange.build(pieces)

    def union(self, other: "VersionRange") -> "VersionRange":
        if self is other:
            return self
        return _union(self, other)

    def complement(self) -> "VersionRange":
        return _complement(self)

    def _complement_uncached(self) -> "VersionRange":
        if not self.intervals:
            return ANY
        gaps: list[Interval] = []
        edge: Optional[Version] = None
        edge_inclusive = True  # 下方无界，左端天然闭合
        for interval in self.intervals:
            if interval.lower is not None:
                gap = Interval(edge, edge_inclusive, interval.lower,
                               not interval.lower_inclusive)
                if not gap.is_empty:
                    gaps.append(gap)
            edge = interval.upper
            edge_inclusive = not interval.upper_inclusive
            if interval.upper is None:
                return VersionRange.build(gaps)
        if edge is not None:
            gaps.append(Interval(edge, edge_inclusive, None, False))
        return VersionRange.build(gaps)

    def difference(self, other: "VersionRange") -> "VersionRange":
        return _intersect(self, _complement(other))

    def is_subset_of(self, other: "VersionRange") -> bool:
        return _is_subset(self, other)

    def is_disjoint(self, other: "VersionRange") -> bool:
        return _intersect(self, other).is_empty

    def __and__(self, other: "VersionRange") -> "VersionRange":
        return self.intersect(other)

    def __or__(self, other: "VersionRange") -> "VersionRange":
        return self.union(other)

    def __invert__(self) -> "VersionRange":
        return self.complement()

    # -- 渲染 -------------------------------------------------------------
    def render(self) -> str:
        if self.is_empty:
            return "<没有版本>"
        if self.is_any:
            return "*"
        return " 或 ".join(interval.render() for interval in self.intervals)

    def __str__(self) -> str:
        return self.render()

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"VersionRange({self.render()})"


# 区间是不可变的、可哈希的，所以可以直接把集合运算的结果记下来。求解器里同一
# 对区间会被反复问到（每条不相容每轮传播都要问一次），缓存把这一步从几万次降
# 到几百次。
@lru_cache(maxsize=16384)
def _intersect(left: "VersionRange", right: "VersionRange") -> "VersionRange":
    if len(left.intervals) > len(right.intervals):
        left, right = right, left
    return left._intersect_uncached(right)


@lru_cache(maxsize=8192)
def _union(left: "VersionRange", right: "VersionRange") -> "VersionRange":
    return VersionRange.build(list(left.intervals) + list(right.intervals))


@lru_cache(maxsize=8192)
def _complement(value: "VersionRange") -> "VersionRange":
    return value._complement_uncached()


@lru_cache(maxsize=32768)
def _is_subset(left: "VersionRange", right: "VersionRange") -> bool:
    if left is right:
        return True
    if not left.intervals:
        return True
    if not right.intervals:
        return False
    return _intersect(left, _complement(right)).is_empty


def _sort_key(interval: Interval):
    return (
        0 if interval.lower is None else 1,
        interval.lower if interval.lower is not None else Version(0),
        0 if interval.lower_inclusive else 1,
    )


ANY = VersionRange((Interval(),))
NOTHING = VersionRange(())


# ---------------------------------------------------------------------------
# 约束语法
# ---------------------------------------------------------------------------
_CARET = "^"
_TILDE = "~"
_COMPARATORS = (">=", "<=", "!=", "==", ">", "<", "=")


def parse_constraint(text: str) -> VersionRange:
    """把 `^1.2` / `>=1.0, <2.0` / `1.2.*` / `*` 这类写法解析成区间集合。

    空格或逗号分隔表示"并且"，`||` 表示"或者"。
    """
    if text is None:
        return ANY
    raw = text.strip()
    if not raw:
        return ANY
    alternatives = [piece.strip() for piece in raw.split("||")]
    result = NOTHING
    for piece in alternatives:
        result = result.union(_parse_conjunction(piece))
    return result


def _parse_conjunction(text: str) -> VersionRange:
    if not text:
        return ANY
    tokens = [token for token in re.split(r"[,\s]+", text) if token]
    result = ANY
    for token in tokens:
        result = result.intersect(_parse_atom(token))
        if result.is_empty:
            return NOTHING
    return result


def _parse_atom(token: str) -> VersionRange:
    if token in ("*", "x", "X", "any"):
        return ANY

    for operator in _COMPARATORS:
        if token.startswith(operator):
            return _comparison(operator, token[len(operator):], token)

    if token.startswith(_CARET):
        return _caret(token[1:], token)
    if token.startswith(_TILDE):
        return _tilde(token[1:], token)

    if token.endswith(".*"):
        return _wildcard(token[:-2], token)
    if token.endswith(".x") or token.endswith(".X"):
        return _wildcard(token[:-2], token)

    version = _partial(token, token)
    return VersionRange.exact(version)


def _segments(text: str) -> int:
    """版本号里"数字段"有几段——预发布标识里的点不算。"""
    head = re.split(r"[-+]", text.strip(), maxsplit=1)[0]
    return len([piece for piece in head.split(".") if piece != ""])


def _partial(text: str, original: str) -> Version:
    raw = text.strip()
    if not raw:
        raise VersionError(f"约束里缺少版本号：{original!r}")
    try:
        return Version.parse(raw)
    except VersionError as exc:
        raise VersionError(f"看不懂的约束 {original!r}：{exc.message}") from None


def _comparison(operator: str, rest: str, original: str) -> VersionRange:
    version = _partial(rest, original)
    if operator in ("=", "=="):
        return VersionRange.exact(version)
    if operator == "!=":
        return VersionRange.exact(version).complement()
    if operator == ">=":
        return VersionRange.at_least(version, True)
    if operator == ">":
        return VersionRange.at_least(version, False)
    if operator == "<=":
        return VersionRange.at_most(version, True)
    if operator == "<":
        return VersionRange.at_most(version, False)
    raise VersionError(f"看不懂的运算符：{operator!r}")  # pragma: no cover


def _caret(rest: str, original: str) -> VersionRange:
    """`^1.2.3` = >=1.2.3 <2.0.0；`^0.2.3` = >=0.2.3 <0.3.0；`^0.0.3` = >=0.0.3 <0.0.4。"""
    text = rest.strip()
    if not text:
        raise VersionError(f"^ 后面缺少版本号：{original!r}")
    base = _partial(text, original)
    filled = _segments(text)
    if base.major > 0 or filled == 1:
        upper = base.bump_major()
    elif base.minor > 0 or filled == 2:
        upper = base.bump_minor()
    else:
        upper = base.bump_patch()
    return VersionRange((Interval(base, True, upper, False),))


def _tilde(rest: str, original: str) -> VersionRange:
    """`~1.2.3` = >=1.2.3 <1.3.0；`~1.2` = >=1.2.0 <1.3.0；`~1` = >=1.0.0 <2.0.0。"""
    text = rest.strip()
    if not text:
        raise VersionError(f"~ 后面缺少版本号：{original!r}")
    base = _partial(text, original)
    upper = base.bump_major() if _segments(text) == 1 else base.bump_minor()
    return VersionRange((Interval(base, True, upper, False),))


def _wildcard(prefix: str, original: str) -> VersionRange:
    parts = [piece for piece in prefix.split(".") if piece != ""]
    if not parts:
        return ANY
    if len(parts) > 2:
        raise VersionError(f"通配符最多写两段：{original!r}")
    numbers = []
    for piece in parts:
        if not _NUMERIC.match(piece):
            raise VersionError(f"通配符前面必须是数字：{original!r}")
        numbers.append(int(piece))
    lower = Version(*numbers) if len(numbers) == 3 else Version(numbers[0], numbers[1] if len(numbers) > 1 else 0)
    if len(numbers) == 1:
        upper = lower.bump_major()
    else:
        upper = lower.bump_minor()
    return VersionRange((Interval(lower, True, upper, False),))


def caret_of(version: Version) -> VersionRange:
    return _caret(str(version), str(version))


def _short(version: Version) -> str:
    """`2.0.0` 写成 `2`，`2.1.0` 写成 `2.1`——只在结尾的段全是零时省。"""
    if version.patch == 0 and version.minor == 0 and not version.prerelease:
        return str(version.major)
    if version.patch == 0 and not version.prerelease:
        return f"{version.major}.{version.minor}"
    return str(version)


def format_constraint(constraint: VersionRange) -> str:
    """把区间还原成最接近原意的写法，用于显示。

    人是照着 `^2.0` 写需求的，报错时却看到 `>=2.0.0, <3.0.0` 会愣一下。
    单区间且上界正好是下界往上抬一位时，就还原成 `^` / `~` 的写法。
    """
    if constraint.is_empty:
        return "<没有版本>"
    if constraint.is_any:
        return "*"
    exact = constraint.exact_version
    if exact is not None:
        return str(exact)
    if len(constraint.intervals) == 1:
        only = constraint.intervals[0]
        if (only.lower is not None and only.upper is not None
                and only.lower_inclusive and not only.upper_inclusive
                and not only.lower.prerelease):
            lower = only.lower
            if only.upper == lower.bump_major():
                return "^" + _short(lower)
            if only.upper == lower.bump_minor():
                # `~1.2` 就是 `>=1.2.0, <1.3.0`；补丁号非零时得写全 `~1.2.3`
                return f"~{lower.major}.{lower.minor}" if not lower.patch else f"~{lower}"
    return constraint.render()


def format_requirement(name: str, constraint: VersionRange) -> str:
    """把约束还原成尽量短的写法，用于显示。"""
    if constraint.is_any:
        return name
    return f"{name} {format_constraint(constraint)}"


def sort_versions(versions: Sequence[Version], reverse: bool = True) -> list[Version]:
    return sorted(versions, reverse=reverse)
