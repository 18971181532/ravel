"""registry 与需求文件：加载、校验、报错的位置信息。"""

import json
import os
import tempfile
import unittest

from ravel.errors import RegistryError
from ravel.registry import (
    Package, Registry, Release, Requirement, dump_requirements, load_requirements,
    parse_requirements,
)
from ravel.versions import Version, VersionRange, parse_constraint

MINIMAL = {
    "packages": {
        "core": {"releases": [{"version": "1.0.0"}, {"version": "2.0.0"}]},
        "web": {"releases": [{"version": "1.0.0",
                              "dependencies": {"core": "^1.0"}}]},
    }
}


def build(data=None, source="<测试>"):
    return Registry.from_dict(data or MINIMAL, source=source)


class LoadTests(unittest.TestCase):
    def test_packages_are_sorted_newest_first(self):
        registry = build()
        self.assertEqual([str(v) for v in registry.require("core").versions],
                         ["2.0.0", "1.0.0"])

    def test_dependencies_are_parsed(self):
        registry = build()
        release = registry.require("web").release(Version.parse("1.0.0"))
        self.assertEqual(len(release.dependencies), 1)
        name, constraint = release.dependencies[0]
        self.assertEqual(name, "core")
        self.assertEqual(constraint, parse_constraint("^1.0"))

    def test_dependency_names_helper(self):
        registry = build()
        release = registry.require("web").release(Version.parse("1.0.0"))
        self.assertEqual(release.dependency_names(), ["core"])
        self.assertEqual(release.dependency_on("core"), parse_constraint("^1.0"))
        self.assertIsNone(release.dependency_on("nope"))

    def test_yanked_flag(self):
        registry = build({"packages": {"a": {"releases": [
            {"version": "1.0.0", "yanked": True},
            {"version": "0.9.0"},
        ]}}})
        package = registry.require("a")
        self.assertTrue(package.release(Version.parse("1.0.0")).yanked)
        self.assertEqual([str(r.version) for r in package.usable()], ["0.9.0"])

    def test_description_is_kept(self):
        registry = build({"packages": {"a": {
            "description": "一个包", "releases": [{"version": "1.0.0"}]}}})
        self.assertEqual(registry.require("a").description, "一个包")

    def test_source_is_recorded(self):
        self.assertEqual(build(source="x.json").source, "x.json")

    def test_duplicate_version_is_rejected(self):
        with self.assertRaises(RegistryError) as caught:
            build({"packages": {"a": {"releases": [
                {"version": "1.0.0"}, {"version": "1.0.0"}]}}})
        self.assertIn("不止一次", caught.exception.message)

    def test_duplicate_package_name_is_rejected(self):
        with self.assertRaises(RegistryError):
            Registry([Package("a", [Release(Version(1, 0, 0))]),
                      Package("A", [Release(Version(2, 0, 0))])])

    def test_missing_packages_key(self):
        with self.assertRaises(RegistryError) as caught:
            Registry.from_dict({})
        self.assertIn("packages", caught.exception.message)

    def test_packages_must_be_an_object(self):
        with self.assertRaises(RegistryError):
            Registry.from_dict({"packages": []})

    def test_release_must_have_version(self):
        with self.assertRaises(RegistryError) as caught:
            build({"packages": {"a": {"releases": [{"dependencies": {}}]}}})
        self.assertIn("version", caught.exception.message)

    def test_release_list_must_not_be_empty(self):
        with self.assertRaises(RegistryError):
            build({"packages": {"a": {"releases": []}}})

    def test_release_list_must_be_a_list(self):
        with self.assertRaises(RegistryError):
            build({"packages": {"a": {"releases": {}}}})

    def test_dependencies_must_be_an_object(self):
        with self.assertRaises(RegistryError):
            build({"packages": {"a": {"releases": [
                {"version": "1.0.0", "dependencies": []}]}}})

    def test_bad_package_name(self):
        with self.assertRaises(RegistryError) as caught:
            build({"packages": {"-bad": {"releases": [{"version": "1.0.0"}]}}})
        self.assertIn("包名不合法", caught.exception.message)

    def test_bad_dependency_name(self):
        with self.assertRaises(RegistryError):
            build({"packages": {"a": {"releases": [
                {"version": "1.0.0", "dependencies": {"###": "*"}}]}}})

    def test_bad_dependency_constraint_names_the_owner(self):
        with self.assertRaises(RegistryError) as caught:
            build({"packages": {"a": {"releases": [
                {"version": "1.0.0", "dependencies": {"b": "^^1"}}]}}})
        self.assertIn("a", caught.exception.message)

    def test_bad_json(self):
        with self.assertRaises(RegistryError) as caught:
            Registry.from_json_text("{oops")
        self.assertIn("不是合法 JSON", caught.exception.message)

    def test_load_from_file(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(MINIMAL, stream)
            registry = Registry.load(path)
            self.assertEqual(registry.stats(), (2, 3))
            self.assertEqual(registry.source, path)
        finally:
            os.unlink(path)

    def test_load_missing_file(self):
        with self.assertRaises(RegistryError):
            Registry.load("no-such-file-ravel.json")


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.registry = build()

    def test_contains_is_case_insensitive(self):
        self.assertIn("CORE", self.registry)
        self.assertNotIn("nope", self.registry)

    def test_get_returns_none_for_unknown(self):
        self.assertIsNone(self.registry.get("nope"))

    def test_require_raises_for_unknown(self):
        with self.assertRaises(RegistryError):
            self.registry.require("nope")

    def test_names_are_sorted(self):
        self.assertEqual(self.registry.names(), ["core", "web"])

    def test_stats(self):
        self.assertEqual(self.registry.stats(), (2, 3))
        self.assertEqual(self.registry.release_count, 3)

    def test_release_of_missing_version(self):
        with self.assertRaises(RegistryError):
            self.registry.require("core").release(Version.parse("9.9.9"))

    def test_display_name_keeps_original_case(self):
        registry = build({"packages": {"MyPkg": {"releases": [{"version": "1.0.0"}]}}})
        self.assertEqual(registry.names(), ["MyPkg"])
        self.assertIsNotNone(registry.get("mypkg"))


class RequirementTests(unittest.TestCase):
    def test_plain_line(self):
        requirements = parse_requirements("core ^1.0")
        self.assertEqual(len(requirements), 1)
        self.assertEqual(requirements[0].name, "core")
        self.assertEqual(requirements[0].constraint, parse_constraint("^1.0"))
        self.assertEqual(requirements[0].line, 1)
        self.assertEqual(requirements[0].text, "^1.0")

    def test_bare_name_means_any(self):
        self.assertTrue(parse_requirements("core")[0].constraint.is_any)

    def test_comments_and_blank_lines(self):
        text = "# 头部注释\n\ncore ^1.0\n  \nweb ^2.0  # 行尾注释\n"
        requirements = parse_requirements(text)
        self.assertEqual([item.name for item in requirements], ["core", "web"])
        self.assertEqual([item.line for item in requirements], [3, 5])

    def test_duplicate_name_is_reported_with_both_lines(self):
        with self.assertRaises(RegistryError) as caught:
            parse_requirements("core ^1.0\nweb ^1.0\ncore ^2.0", source="需求")
        message = caught.exception.message
        self.assertIn("第 3 行", message)
        self.assertIn("第 1 行", message)

    def test_bad_constraint_is_reported_with_line(self):
        with self.assertRaises(RegistryError) as caught:
            parse_requirements("core ^1.0\nweb ^^2", source="需求")
        self.assertIn("第 2 行", caught.exception.message)

    def test_garbage_line(self):
        with self.assertRaises(RegistryError):
            parse_requirements("###")

    def test_empty_input(self):
        with self.assertRaises(RegistryError) as caught:
            parse_requirements("# 只有注释")
        self.assertIn("没有任何需求", caught.exception.message)

    def test_equals_is_accepted_as_exact(self):
        self.assertEqual(parse_requirements("core = 1.0.0")[0].constraint,
                         VersionRange.exact(Version.parse("1.0.0")))

    def test_str_prefers_the_original_text(self):
        self.assertEqual(str(parse_requirements("core ^1.2")[0]), "core ^1.2")

    def test_str_falls_back_to_the_range(self):
        item = Requirement("core", parse_constraint("^1.0"))
        self.assertEqual(str(item), "core ^1")

    def test_str_of_any_requirement_is_just_the_name(self):
        self.assertEqual(str(Requirement("core", VersionRange.any())), "core")

    def test_dump(self):
        requirements = parse_requirements("core ^1.0\nweb ^2.0")
        self.assertEqual(dump_requirements(requirements), "core ^1.0\nweb ^2.0\n")

    def test_load_requirements_from_file(self):
        handle, path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write("core ^1.0\n")
            self.assertEqual(load_requirements(path)[0].name, "core")
        finally:
            os.unlink(path)

    def test_load_missing_requirements(self):
        with self.assertRaises(RegistryError):
            load_requirements("no-such-requirements.txt")


class PackageTests(unittest.TestCase):
    def test_length_and_versions(self):
        package = Package("a", [Release(Version(1, 0, 0)), Release(Version(2, 0, 0))])
        self.assertEqual(len(package), 2)
        self.assertEqual([str(v) for v in package.versions], ["2.0.0", "1.0.0"])

    def test_repr(self):
        package = Package("a", [Release(Version(1, 0, 0))])
        self.assertIn("a", repr(package))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
