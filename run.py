#!/usr/bin/env python3
"""不装包也能用的入口：`python run.py`。

不带参数时把内置场景全跑一遍；带参数时原样转给命令行。
等价于 `python -m ravel`，只是省去了设置 PYTHONPATH 的麻烦。
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    sys.path.insert(0, ROOT)
    from ravel.cli import main as cli_main

    arguments = sys.argv[1:]
    if not arguments:
        arguments = ["scenario", "--all"]
    return cli_main(arguments)


if __name__ == "__main__":
    sys.exit(main())
