#!/usr/bin/env python3
"""跑全部测试：`python run_tests.py`，加 -v 看每条用例。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    sys.path.insert(0, ROOT)
    verbosity = 2 if "-v" in sys.argv[1:] else 1
    suite = unittest.defaultTestLoader.discover(
        os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
