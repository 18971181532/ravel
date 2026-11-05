"""锁文件：写出去、读回来、以及在新 registry 上核对。"""

import json
import os
import tempfile
import unittest

from ravel import samples
from ravel.errors import LockError
from ravel.lock import (
    LOCK_VERSION, Check, Lock, check, from_json, from_solution, load,
    load_requirements_text, save, to_json,
)
from ravel.registry import Registry, parse_requirements
from ravel.solver import Solver
from ravel.versions import Version


def solved(text, registry=None):
    registry = registry or samples.build()
    return Solver(registry, parse_requirements(text, source="<测试>")).solve()


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.lock = from_solution(solved("web ^1.0\nmetrics ^1.0"))

    def test_records_every_package(self):
        self.assertEqual(len(self.lock), 4)
        self.assertEqual(self.lock.packages["http"], "1.8.0")

    def test_records_the_requirements_verbatim(self):
        self.assertEqual(self.lock.requirements, ["web ^1.0", "metrics ^1.0"])

    def test_records_the_registry_source(self):
        self.assertEqual(self.lock.registry, "内置示例")

    def test_items_are_sorted(self):
        self.assertEqual([name for name, _ in self.lock.items()],
                         ["http", "log", "metrics", "web"])

    def test_version_of(self):
        self.assertEqual(self.lock.version_of("web"), Version.parse("1.0.0"))
        self.assertIsNone(self.lock.version_of("nope"))

    def test_json_round_trip(self):
        again = from_json(to_json(self.lock))
        self.assertEqual(again.packages, self.lock.packages)
        self.assertEqual(again.requirements, self.lock.requirements)

    def test_json_is_sorted_and_indented(self):
        text = to_json(self.lock)
        self.assertIn('"lock_version": 1', text)
        self.assertTrue(text.endswith("\n"))
        payload = json.loads(text)
        self.assertEqual(list(payload["packages"]), ["http", "log", "metrics", "web"])

    def test_save_and_load(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "ravel.lock")
        try:
            save(self.lock, path)
            again = load(path)
            self.assertEqual(again.packages, self.lock.packages)
        finally:
            os.unlink(path)
            os.rmdir(directory)

    def test_load_missing_file(self):
        with self.assertRaises(LockError):
            load("no-such-ravel.lock")

    def test_requirements_text_can_be_reparsed(self):
        requirements = load_requirements_text(self.lock)
        self.assertEqual([str(item) for item in requirements], self.lock.requirements)

    def test_empty_requirements_round_trip(self):
        self.assertEqual(load_requirements_text(Lock({"a": "1.0.0"})), [])


class ParseErrorTests(unittest.TestCase):
    def test_bad_json(self):
        with self.assertRaises(LockError) as caught:
            from_json("{oops")
        self.assertIn("不是合法 JSON", caught.exception.message)

    def test_top_level_must_be_an_object(self):
        with self.assertRaises(LockError):
            from_json("[]")

    def test_wrong_lock_version(self):
        with self.assertRaises(LockError) as caught:
            from_json(json.dumps({"lock_version": 99, "packages": {}}))
        self.assertIn("lock_version", caught.exception.message)

    def test_missing_packages(self):
        with self.assertRaises(LockError):
            from_json(json.dumps({"lock_version": LOCK_VERSION}))

    def test_version_must_be_a_string(self):
        with self.assertRaises(LockError):
            from_json(json.dumps({"lock_version": LOCK_VERSION,
                                  "packages": {"a": 100}}))

    def test_version_must_parse(self):
        with self.assertRaises(Exception):
            from_json(json.dumps({"lock_version": LOCK_VERSION,
                                  "packages": {"a": "not-a-version"}}))

    def test_requirements_must_be_strings(self):
        with self.assertRaises(LockError):
            from_json(json.dumps({"lock_version": LOCK_VERSION, "packages": {},
                                  "requirements": [1, 2]}))

    def test_requirements_default_to_empty(self):
        lock = from_json(json.dumps({"lock_version": LOCK_VERSION,
                                     "packages": {"a": "1.0.0"}}))
        self.assertEqual(lock.requirements, [])


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.registry = samples.build()
        self.requirements = parse_requirements("web ^1.0\nmetrics ^1.0", source="<测试>")
        self.lock = from_solution(solved("web ^1.0\nmetrics ^1.0", self.registry))

    def test_fresh_lock_is_ok(self):
        result = check(self.lock, self.registry, self.requirements)
        self.assertTrue(result.ok, result.problems)

    def test_ok_without_requirements(self):
        self.assertTrue(check(self.lock, self.registry).ok)

    def test_notes_mention_newer_versions(self):
        result = check(self.lock, self.registry)
        self.assertTrue(any("有更新的版本" in note for note in result.notes))

    def test_missing_package(self):
        lock = Lock({"ghost": "1.0.0"})
        result = check(lock, self.registry)
        self.assertFalse(result.ok)
        self.assertIn("已经没有 ghost", result.problems[0])

    def test_missing_version(self):
        lock = Lock({"core": "9.9.9"})
        result = check(lock, self.registry)
        self.assertIn("已经不在 registry 里", result.problems[0])

    def test_yanked_version(self):
        lock = Lock({"http": "1.9.0"})
        result = check(lock, self.registry)
        self.assertTrue(any("撤回" in item for item in result.problems))

    def test_dependency_satisfied(self):
        self.assertTrue(check(Lock({"web": "1.0.0", "http": "1.8.0",
                                    "log": "1.2.0"}), self.registry).ok)

    def test_dependency_without_target(self):
        result = check(Lock({"web": "1.0.0"}), self.registry)
        self.assertTrue(any("但锁文件里没有" in item for item in result.problems))

    def test_dependency_with_wrong_version(self):
        result = check(Lock({"web": "1.0.0", "http": "2.0.0", "log": "1.2.0"}),
                       self.registry)
        self.assertTrue(any("锁文件里却是" in item for item in result.problems))

    def test_requirement_mismatch(self):
        other = parse_requirements("web ^1.0", source="<测试>")
        result = check(self.lock, self.registry, other)
        self.assertTrue(any("对不上" in item for item in result.problems))

    def test_requirement_unsatisfied_by_lock(self):
        requirements = parse_requirements("web ^1.0\nmetrics ^1.0", source="<测试>")
        lock = Lock(dict(self.lock.packages, metrics="2.0.0"), self.lock.requirements)
        result = check(lock, self.registry, requirements)
        self.assertTrue(any("不兼容" in item for item in result.problems))

    def test_requirement_without_a_locked_version(self):
        requirements = parse_requirements("web ^1.0\nmetrics ^1.0\ncrypto ^1.0",
                                          source="<测试>")
        result = check(self.lock, self.registry, requirements)
        self.assertFalse(result.ok)

    def test_render_lists_problems(self):
        result = Check(["坏了"], ["还行"])
        text = result.render()
        self.assertIn("✗ 坏了", text)
        self.assertIn("· 还行", text)

    def test_render_of_a_clean_check(self):
        self.assertIn("仍然成立", Check().render())
        self.assertIn("✓", Check().render())

    def test_ok_property(self):
        self.assertTrue(Check().ok)
        self.assertFalse(Check(["x"]).ok)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
