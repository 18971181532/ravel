"""命令行入口。

八个子命令，覆盖日常真正会用到的动作：

    solve     求解并列出选了哪些版本
    explain   装不上时，一步步讲清楚为什么
    why       某个包为什么是这一版
    show      registry 里有什么
    scenario  跑内置的演示场景
    bench     测一下求解要多久
    lock      把这次的结果钉成锁文件
    check     核对锁文件在今天还成不成立

需求既可以直接写在命令行上（`ravel solve "web ^1.0"`），也可以给文件路径，
或者用 `-` 从标准输入读。退出码：0 成功，1 无解，2 输入有问题。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional, Sequence

from . import samples
from .errors import RavelError, RegistryError
from .explain import explain as explain_failure
from .lock import DEFAULT_PATH as DEFAULT_LOCK
from .lock import check as check_lock
from .lock import from_solution, load as load_lock, save as save_lock
from .registry import Registry, Requirement, parse_requirements
from .report import display_width, pad, render as render_solution, why as why_package
from .solver import SolveFailure, Solver
from .versions import VersionRange

PROGRAM = "ravel"


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------
def _read_requirements(items: Sequence[str]) -> List[Requirement]:
    if not items:
        raise RegistryError(
            '没有给需求：写一条（例如 "web ^1.0"）、给一个需求文件，或者用 - 从标准输入读')
    chunks: List[str] = []
    for item in items:
        if item == "-":
            chunks.append(sys.stdin.read())
        elif os.path.isfile(item):
            with open(item, encoding="utf-8") as handle:
                chunks.append(handle.read())
        else:
            chunks.append(item)
    return parse_requirements("\n".join(chunks), source="命令行")


def _registry(args: argparse.Namespace) -> Registry:
    path = getattr(args, "registry", None)
    if path:
        return Registry.load(path)
    return samples.build()


def _emit(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------
def command_solve(args: argparse.Namespace) -> int:
    registry = _registry(args)
    requirements = _read_requirements(args.requirements)
    try:
        solution = Solver(registry, requirements).solve()
    except SolveFailure as failure:
        if args.json:
            _emit({"ok": False, "stats": failure.stats.as_dict()})
        else:
            print(explain_failure(failure, registry, requirements).render())
        return 1
    if args.json:
        _emit({"ok": True, "packages": solution.as_dict(),
               "stats": solution.stats.as_dict()})
    else:
        print(render_solution(solution))
    return 0


def command_explain(args: argparse.Namespace) -> int:
    registry = _registry(args)
    requirements = _read_requirements(args.requirements)
    try:
        solution = Solver(registry, requirements).solve()
    except SolveFailure as failure:
        explanation = explain_failure(failure, registry, requirements)
        if args.json:
            _emit({
                "ok": False,
                "steps": [{"number": step.number, "body": step.body,
                           "parents": list(step.parents)} for step in explanation.steps],
                "suggestions": explanation.suggestions,
                "stats": failure.stats.as_dict(),
            })
        elif args.tree:
            print(explanation.tree())
        else:
            print(explanation.render())
        return 1
    if args.json:
        _emit({"ok": True, "packages": solution.as_dict()})
    else:
        print("这组需求是有解的，没什么可解释的：")
        print()
        print(render_solution(solution))
    return 0


def command_why(args: argparse.Namespace) -> int:
    registry = _registry(args)
    requirements = _read_requirements(args.requirements)
    try:
        solution = Solver(registry, requirements).solve()
    except SolveFailure as failure:
        print(explain_failure(failure, registry, requirements).render())
        return 1
    package = registry.get(args.package)
    if package is None:
        print(f"registry 里没有 {args.package}", file=sys.stderr)
        return 2
    print(why_package(solution, package.name))
    return 0


def command_show(args: argparse.Namespace) -> int:
    registry = _registry(args)
    if args.json:
        _emit({
            "source": registry.source,
            "packages": [
                {
                    "name": package.name,
                    "description": package.description,
                    "releases": [
                        {"version": str(release.version), "yanked": release.yanked,
                         "dependencies": {name: str(constraint)
                                          for name, constraint in release.dependencies}}
                        for release in package.releases
                    ],
                }
                for package in (registry.get(name) for name in registry.names())
            ],
        })
        return 0
    packages, releases = registry.stats()
    print(f"{registry.source}：{packages} 个包，{releases} 个版本")
    width = max((len(name) for name in registry.names()), default=0)
    for name in registry.names():
        package = registry.get(name)
        marks = []
        for release in package.releases:
            mark = str(release.version)
            if release.yanked:
                mark += "(撤回)"
            marks.append(mark)
        print(f"  {name.ljust(width)}  {' '.join(marks)}")
    return 0


def command_scenario(args: argparse.Namespace) -> int:
    registry = _registry(args)
    everything = [item[0] for item in samples.SCENARIOS]
    wanted = everything if (args.all or not args.name) else [args.name]
    code = 0
    for name in wanted:
        row = next((item for item in samples.SCENARIOS if item[0] == name), None)
        if row is None:
            print(f"没有这个场景：{name}（可选：{'、'.join(i[0] for i in samples.SCENARIOS)}）",
                  file=sys.stderr)
            return 2
        _, text, note = row
        requirements = parse_requirements(text, source=f"场景 {name}")
        print(f"### {name} —— {note}")
        print(f"需求：{text.replace(chr(10), ' + ')}")
        print()
        try:
            solution = Solver(registry, requirements).solve()
        except SolveFailure as failure:
            print(explain_failure(failure, registry, requirements).render())
            code = 1
        else:
            print(render_solution(solution))
        print()
    return code


def command_bench(args: argparse.Namespace) -> int:
    registry = _registry(args)
    repeats = max(args.repeat, 1)
    print(f"{registry.source}：{registry.stats()[0]} 个包，{registry.stats()[1]} 个版本；"
          f"每组取 {repeats} 次里最快的一次")
    print()
    print(f"  {pad('场景', 14)}{'用时':>9}   结果")
    for name, text, _ in samples.SCENARIOS:
        requirements = parse_requirements(text, source=f"场景 {name}")
        best, outcome = None, ""
        for _ in range(repeats):
            started = time.perf_counter()
            try:
                solution = Solver(registry, requirements).solve()
            except SolveFailure as failure:
                elapsed = time.perf_counter() - started
                outcome = f"无解（{len(explain_failure(failure, registry, requirements).steps)} 步推导）"
            else:
                elapsed = time.perf_counter() - started
                outcome = f"{len(solution)} 个包，{solution.stats.backtracks} 次回退"
            best = elapsed if best is None else min(best, elapsed)
        print(f"  {pad(name, 14)}{best * 1000:>8.2f}ms   {outcome}")
    synthetic = samples.synthetic(args.packages, args.versions)
    wanted = [Requirement(name, VersionRange.any()) for name in synthetic.names()]
    best, outcome = None, ""
    for _ in range(repeats):
        started = time.perf_counter()
        try:
            solution = Solver(synthetic, wanted).solve()
            outcome = f"{len(solution)} 个包，{solution.stats.backtracks} 次回退"
        except SolveFailure:  # pragma: no cover - 合成数据总是有解
            outcome = "无解"
        elapsed = time.perf_counter() - started
        best = elapsed if best is None else min(best, elapsed)
    count, releases = synthetic.stats()
    print(f"  {pad('合成', 14)}{best * 1000:>8.2f}ms   "
          f"{count} 个包 × {args.versions} 版（{releases} 个版本），{outcome}")
    return 0


def command_lock(args: argparse.Namespace) -> int:
    registry = _registry(args)
    requirements = _read_requirements(args.requirements)
    try:
        solution = Solver(registry, requirements).solve()
    except SolveFailure as failure:
        print(explain_failure(failure, registry, requirements).render())
        return 1
    lock = from_solution(solution, registry=registry.source)
    save_lock(lock, args.output)
    print(f"已写入 {args.output}：{len(lock)} 个包")
    for name, version in lock.items():
        print(f"  {name} {version}")
    return 0


def command_check(args: argparse.Namespace) -> int:
    registry = _registry(args)
    lock = load_lock(args.lock)
    requirements = _read_requirements(args.requirements) if args.requirements else None
    result = check_lock(lock, registry, requirements)
    print(f"核对 {args.lock}（{len(lock)} 个包）")
    print(result.render())
    return 0 if result.ok else 1


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Ravel —— 依赖版本求解器，装不上时说清楚为什么。",
        epilog='例子：ravel solve "web ^1.0" "metrics ^1.0"',
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} 1.0.0")
    subparsers = parser.add_subparsers(dest="command")

    def add(name: str, help_text: str, handler, registry: bool = True) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        sub.set_defaults(handler=handler)
        if registry:
            sub.add_argument("--registry", metavar="路径",
                             help="registry 的 JSON 文件；不给就用内置示例生态")
        return sub

    solve = add("solve", "求解并列出选了哪些版本", command_solve)
    solve.add_argument("requirements", nargs="*", metavar="需求",
                       help='如 "web ^1.0"，也可以给需求文件路径或 -')
    solve.add_argument("--json", action="store_true", help="输出 JSON")

    explain = add("explain", "无解时一步步讲清楚为什么", command_explain)
    explain.add_argument("requirements", nargs="*", metavar="需求")
    explain.add_argument("--json", action="store_true", help="输出 JSON")
    explain.add_argument("--tree", action="store_true",
                         help="换成树形：从结论往下展开，末梢是事实")

    why = add("why", "某个包为什么是这一版", command_why)
    why.add_argument("package", metavar="包名")
    why.add_argument("requirements", nargs="*", metavar="需求")

    show = add("show", "列出 registry 里的包和版本", command_show)
    show.add_argument("--json", action="store_true", help="输出 JSON")

    scenario = add("scenario", "跑内置的演示场景", command_scenario)
    scenario.add_argument("name", nargs="?", metavar="场景名",
                          help="不给就用 --all")
    scenario.add_argument("--all", action="store_true", help="跑全部场景")

    bench = add("bench", "测一下求解要多久", command_bench)
    bench.add_argument("--repeat", type=int, default=5, metavar="N", help="每组重复几次")
    bench.add_argument("--packages", type=int, default=60, metavar="N", help="合成数据包数")
    bench.add_argument("--versions", type=int, default=4, metavar="N", help="合成数据版本数")

    lock = add("lock", "把这次求解的结果写成锁文件", command_lock)
    lock.add_argument("requirements", nargs="*", metavar="需求")
    lock.add_argument("-o", "--output", default=DEFAULT_LOCK, metavar="路径",
                      help=f"锁文件路径（默认 {DEFAULT_LOCK}）")

    check = add("check", "核对锁文件在今天还成不成立", command_check)
    check.add_argument("requirements", nargs="*", metavar="需求")
    check.add_argument("-l", "--lock", default=DEFAULT_LOCK, metavar="路径",
                       help=f"锁文件路径（默认 {DEFAULT_LOCK}）")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return handler(args)
    except RavelError as error:
        print(error.render(), file=sys.stderr)
        return 2
    except BrokenPipeError:  # pragma: no cover - 管道被关掉
        return 0
    except KeyboardInterrupt:  # pragma: no cover
        return 130
