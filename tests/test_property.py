"""拿暴力枚举给求解器当裁判，顺带验一遍区间代数的定律。

两部分：

* **对拍**：随机造一批小 registry，穷举所有"装哪个版本/不装"的组合，看有没有
  可行解；再拿求解器的结论去比。只要有一边判错，这个测试就红。
* **定律**：交集交换、补集对合、德摩根、以及"交集 + 差集"正好把集合切成两半。
  这些是求解器全部判断的地基，错一条就会以很难查的方式歪掉。
"""

import itertools
import random
import unittest

from ravel.registry import Registry, parse_requirements
from ravel.solver import SolveFailure, Solver
from ravel.versions import VersionRange, parse_constraint

UNIVERSE = ["1.0.0", "1.1.0", "1.2.0", "2.0.0", "2.1.0"]
CONSTRAINTS = ["*", "^1.0", "^2.0", ">=1.1", "<2.0", "1.0.0", "!=1.0.0", "^1.1",
               "<=1.1.0", ">=2.0", "~1.1", "1.2.*", "^1.2.0", ">1.0.0", "<=2.0",
               "1.1.0 || >=2.0"]


def random_registry(rng, count):
    names = [f"p{index}" for index in range(count)]
    packages = {}
    for name in names:
        picked = sorted(rng.sample(UNIVERSE, rng.randint(1, 3)))
        releases = []
        for version in picked:
            deps = {other: rng.choice(CONSTRAINTS) for other in names
                    if other != name and rng.random() < 0.35}
            item = {"version": version}
            if deps:
                item["dependencies"] = deps
            if rng.random() < 0.08:
                item["yanked"] = True
            releases.append(item)
        packages[name] = {"releases": releases}
    return Registry.from_dict({"packages": packages}, source="<对拍>")


def brute_force(registry, requirements):
    """穷举：每个包要么不装，要么装某个可用版本。返回第一个可行解。"""
    names = registry.names()
    choices = [[None] + [release.version for release in registry.get(name).usable()]
               for name in names]
    for combo in itertools.product(*choices):
        picked = {name: version for name, version in zip(names, combo)
                  if version is not None}
        if not all(item.name in picked and item.constraint.contains(picked[item.name])
                   for item in requirements):
            continue
        if not all(dep in picked and constraint.contains(picked[dep])
                   for name, version in picked.items()
                   for dep, constraint in registry.get(name).release(version).dependencies):
            continue
        return picked
    return None


def assert_solution_is_valid(case, registry, requirements, solution):
    picked = dict(solution.decisions)
    for item in requirements:
        case.assertIn(item.name, picked, f"需求 {item} 没被满足")
        case.assertTrue(item.constraint.contains(picked[item.name]),
                        f"需求 {item} 被 {picked[item.name]} 违反")
    for name, version in picked.items():
        for dep, constraint in registry.get(name).release(version).dependencies:
            case.assertIn(dep, picked, f"{name} {version} 的依赖 {dep} 没装")
            case.assertTrue(constraint.contains(picked[dep]),
                            f"{name} {version} 要 {dep} {constraint}，却装了 {picked[dep]}")


class BruteForceTests(unittest.TestCase):
    TRIALS = 120

    def test_matches_brute_force(self):
        rng = random.Random(20260101)
        for trial in range(self.TRIALS):
            registry = random_registry(rng, rng.randint(2, 4))
            text = "\n".join(f"{name} {rng.choice(CONSTRAINTS)}"
                             for name in rng.sample(registry.names(), rng.randint(1, 2)))
            requirements = parse_requirements(text, source=f"t{trial}")
            expected = brute_force(registry, requirements)
            with self.subTest(trial=trial, requirements=text):
                try:
                    solution = Solver(registry, requirements).solve()
                except SolveFailure:
                    self.assertIsNone(expected, "求解器说无解，但暴力枚举找到了解")
                else:
                    self.assertIsNotNone(expected, "求解器给了答案，但其实无解")
                    assert_solution_is_valid(self, registry, requirements, solution)

    def test_backtracking_is_needed_sometimes(self):
        """对拍用的这批量数据里，确实存在需要回退才能解出来的用例。"""
        rng = random.Random(20260101)
        backtracks = 0
        for _ in range(self.TRIALS):
            registry = random_registry(rng, rng.randint(2, 4))
            text = "\n".join(f"{name} {rng.choice(CONSTRAINTS)}"
                             for name in rng.sample(registry.names(), rng.randint(1, 2)))
            requirements = parse_requirements(text, source="<对拍>")
            try:
                solution = Solver(registry, requirements).solve()
            except SolveFailure:
                continue
            backtracks += solution.stats.backtracks
        self.assertGreater(backtracks, 0)


class AlgebraLawTests(unittest.TestCase):
    """区间代数的几条定律，逐对验。"""

    RANGES = [
        "*", "1.0.0", "^1.0", "^2.0", ">=1.1", "<2.0", "!=1.2.0",
        ">=1.0 <2.0", "1.0.0 || 2.1.0", ">1.0.0", "<=2.0.0", "~1.1",
    ]

    def setUp(self):
        self.ranges = [parse_constraint(text) for text in self.RANGES]

    def test_intersection_is_commutative(self):
        for left in self.ranges:
            for right in self.ranges:
                self.assertEqual(left.intersect(right), right.intersect(left))

    def test_union_is_commutative(self):
        for left in self.ranges:
            for right in self.ranges:
                self.assertEqual(left.union(right), right.union(left))

    def test_complement_is_an_involution(self):
        for ranges in self.ranges:
            self.assertEqual(ranges.complement().complement(), ranges)

    def test_de_morgan(self):
        for left in self.ranges:
            for right in self.ranges:
                self.assertEqual((left.union(right)).complement(),
                                 left.complement().intersect(right.complement()))

    def test_difference_is_intersection_with_complement(self):
        for left in self.ranges:
            for right in self.ranges:
                self.assertEqual(left.difference(right),
                                 left.intersect(right.complement()))

    def test_difference_and_intersection_split_the_range(self):
        for left in self.ranges:
            for right in self.ranges:
                pieces = left.difference(right).union(left.intersect(right))
                self.assertEqual(pieces, left)

    def test_difference_is_disjoint_from_the_subtrahend(self):
        for left in self.ranges:
            for right in self.ranges:
                self.assertTrue(left.difference(right).is_disjoint(right))

    def test_subset_is_antisymmetric(self):
        for left in self.ranges:
            for right in self.ranges:
                if left.is_subset_of(right) and right.is_subset_of(left):
                    self.assertEqual(left, right)

    def test_subset_matches_membership_on_a_sample(self):
        samples = [rel + ".0.0" for rel in ("1", "2")] + UNIVERSE
        versions = [__import__("ravel").Version.parse(text) for text in samples]
        for left in self.ranges:
            for right in self.ranges:
                if left.is_subset_of(right):
                    for version in versions:
                        if left.contains(version):
                            self.assertTrue(right.contains(version))

    def test_empty_and_any_are_the_extremes(self):
        nothing = VersionRange.nothing()
        any_range = VersionRange.any()
        for ranges in self.ranges:
            self.assertTrue(nothing.is_subset_of(ranges))
            self.assertTrue(ranges.is_subset_of(any_range))
            self.assertEqual(ranges.intersect(nothing), nothing)
            self.assertEqual(ranges.union(any_range), any_range)

    def test_intersection_with_self_is_identity(self):
        for ranges in self.ranges:
            self.assertIs(ranges.intersect(ranges), ranges)


class DeterminismTests(unittest.TestCase):
    def outcome(self, registry, requirements):
        try:
            solution = Solver(registry, requirements).solve()
        except SolveFailure as exc:
            return None, exc.incompatibility.render(), exc.stats.as_dict()
        return solution.as_dict(), None, solution.stats.as_dict()

    def test_same_input_same_output(self):
        # 连失败的那棵树都必须是同一棵，否则"为什么装不上"就不敢信
        rng = random.Random(7)
        for _ in range(20):
            registry = random_registry(rng, 4)
            requirements = parse_requirements("p0 *\np1 ^1.0", source="<测试>")
            first = self.outcome(registry, requirements)
            second = self.outcome(registry, requirements)
            self.assertEqual(first, second)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
