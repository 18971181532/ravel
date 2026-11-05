"""Ravel —— 把"为什么装不上"讲清楚的依赖求解器。

三层东西，彼此不越界：

* :mod:`ravel.versions` —— 版本与版本区间。约束一律解析成"若干区间的并集"，
  之后所有判断都退化成集合运算，不再有语法糖。
* :mod:`ravel.solver` —— PubGrub：传播、决策、冲突消解、失败，一路记下推导树。
* :mod:`ravel.explain` —— 把那棵推导树摊平成一段能读的论证。

外面还有 :mod:`ravel.report`（有解时的账）、:mod:`ravel.lock`（锁文件）、
:mod:`ravel.registry`（包数据）与 :mod:`ravel.cli`（命令行）。
"""

from __future__ import annotations

from typing import Sequence, Union

from .errors import LockError, RavelError, RegistryError, SolveError, VersionError
from .explain import Explanation, Step, explain
from .lock import Lock, check as check_lock
from .registry import (Package, Registry, Release, Requirement,
                       load_requirements, parse_requirements)
from .report import render as render_report
from .report import why as why_package
from .solver import ROOT, Solution, SolveFailure, Solver, Stats, resolve
from .versions import (Interval, Version, VersionRange, format_constraint,
                       parse_constraint)

__version__ = "1.0.0"

__all__ = [
    "Explanation", "Interval", "Lock", "Package", "ROOT", "RavelError",
    "Registry", "RegistryError", "Release", "Requirement", "Solution",
    "SolveError", "SolveFailure", "Solver", "Stats", "Step", "LockError",
    "Version", "VersionError", "VersionRange", "check_lock", "explain",
    "format_constraint", "load_requirements", "parse_constraint",
    "parse_requirements", "render_report", "resolve", "solve", "why_package",
    "__version__",
]


def solve(registry: Registry,
          requirements: Union[str, Sequence[Requirement]]) -> Solution:
    """用一段需求文本（或一组现成的需求）求解。"""
    if isinstance(requirements, str):
        requirements = parse_requirements(requirements, source="<文本>")
    return resolve(registry, requirements)
