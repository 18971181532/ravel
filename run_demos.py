#!/usr/bin/env python3
"""带解说的演示：`python run_demos.py`。

六段：一次平常的求解、一次靠回退救回来的求解、两次无解（一次是约束打架，
一次是包根本不存在）、一条深依赖链、以及预发布版本的规矩。每段都直接跑真的
求解器，输出的是真的结果，没有写死的字符串。
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from ravel import samples                                    # noqa: E402
from ravel.explain import explain                            # noqa: E402
from ravel.registry import parse_requirements                # noqa: E402
from ravel.report import render as render_report             # noqa: E402
from ravel.report import use_utf8_stdout                     # noqa: E402
from ravel.solver import SolveFailure, Solver                 # noqa: E402

WIDTH = 72


def rule(char="="):
    return char * WIDTH


def main() -> int:
    use_utf8_stdout()
    registry = samples.build()
    print(rule())
    print("Ravel 演示：一个会把「为什么装不上」讲出来的依赖求解器")
    print(rule())
    packages, releases = registry.stats()
    print(f"内置生态：{packages} 个包、{releases} 个版本（和 README 里的数字同源）")

    for index, (name, text, note) in enumerate(samples.SCENARIOS, start=1):
        requirements = parse_requirements(text, source=f"场景 {name}")
        print()
        print(rule("-"))
        print(f"[{index}/{len(samples.SCENARIOS)}] {name} —— {note}")
        print(f"需求：{text.replace(chr(10), ' + ')}")
        print()
        try:
            solution = Solver(registry, requirements).solve()
        except SolveFailure as failure:
            explanation = explain(failure, registry, requirements)
            print(explanation.render())
            print()
            print("同一棵推导树的另一种画法：")
            print(explanation.tree())
        else:
            print(render_report(solution))

    print()
    print(rule())
    print("演示结束。想自己试：python run.py solve \"web ^1.0\"")
    print(rule())
    return 0


if __name__ == "__main__":
    sys.exit(main())
