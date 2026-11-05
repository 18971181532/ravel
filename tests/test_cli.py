"""命令行：每个子命令都跑一遍，连退出码一起验。"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from ravel.cli import main
from ravel.lock import load as load_lock


class Runner(unittest.TestCase):
    def run_cli(self, *args, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        saved = None
        if stdin is not None:
            saved, __import__("sys").stdin = __import__("sys").stdin, io.StringIO(stdin)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = main(list(args))
        finally:
            if saved is not None:
                __import__("sys").stdin = saved
        return code, out.getvalue(), err.getvalue()


class BasicsTests(Runner):
    def test_version_flag(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("ravel 1.0.0", out.getvalue())

    def test_no_subcommand_prints_help(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("usage:", out)
        for name in ("solve", "explain", "why", "show", "scenario", "bench", "lock", "check"):
            self.assertIn(name, out)

    def test_missing_requirements_is_reported(self):
        code, _, err = self.run_cli("solve")
        self.assertEqual(code, 2)
        self.assertIn("没有给需求", err)

    def test_missing_registry_file(self):
        code, _, err = self.run_cli("solve", "web ^1.0", "--registry", "no-such.json")
        self.assertEqual(code, 2)
        self.assertIn("找不到 registry", err)

    def test_unknown_subcommand(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            main(["nope"])
        self.assertIn("invalid choice", err.getvalue())


class SolveTests(Runner):
    def test_success(self):
        code, out, _ = self.run_cli("solve", "web ^1.0", "metrics ^1.0")
        self.assertEqual(code, 0)
        self.assertIn("http", out)
        self.assertIn("共 4 个包", out)

    def test_failure_explains_itself(self):
        code, out, _ = self.run_cli("solve", "web ^2.0", "http ^1.0")
        self.assertEqual(code, 1)
        self.assertIn("推导如下", out)
        self.assertIn("结论", out)

    def test_json_output(self):
        code, out, _ = self.run_cli("solve", "web ^1.0", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["packages"]["web"], "1.0.0")
        self.assertIn("backtracks", payload["stats"])

    def test_json_output_on_failure(self):
        code, out, _ = self.run_cli("solve", "web ^2.0", "http ^1.0", "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])

    def test_requirements_from_a_file(self):
        handle, path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write("# 一行注释\nweb ^1.0\n")
            code, out, _ = self.run_cli("solve", path)
            self.assertEqual(code, 0)
            self.assertIn("web", out)
            self.assertIn("1.0.0", out)
        finally:
            os.unlink(path)

    def test_requirements_from_stdin(self):
        code, out, _ = self.run_cli("solve", "-", stdin="web ^1.0\n")
        self.assertEqual(code, 0)
        self.assertIn("http", out)

    def test_custom_registry(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"packages": {"solo": {
                    "releases": [{"version": "1.0.0"}, {"version": "1.5.0"}]}}}, stream)
            code, out, _ = self.run_cli("solve", "solo ^1.0", "--registry", path)
            self.assertEqual(code, 0)
            self.assertIn("1.5.0", out)
        finally:
            os.unlink(path)

    def test_bad_requirement_syntax(self):
        code, _, err = self.run_cli("solve", "web ^^1")
        self.assertEqual(code, 2)
        self.assertIn("错误", err)


class ExplainTests(Runner):
    def test_explains_a_failure(self):
        code, out, _ = self.run_cli("explain", "web ^2.0", "http ^1.0")
        self.assertEqual(code, 1)
        self.assertIn("无解", out)
        self.assertIn("推导如下", out)

    def test_explains_json(self):
        code, out, _ = self.run_cli("explain", "nope ^1.0", "--json")
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["steps"])
        self.assertEqual(payload["steps"][0]["number"], 1)
        self.assertIn("stats", payload)

    def test_solvable_input_is_not_an_error(self):
        code, out, _ = self.run_cli("explain", "web ^1.0")
        self.assertEqual(code, 0)
        self.assertIn("有解", out)


class WhyTests(Runner):
    def test_explains_a_package(self):
        code, out, _ = self.run_cli("why", "api", "api *", "http ^1.0")
        self.assertEqual(code, 0)
        self.assertIn("api 1.0.0", out)
        self.assertIn("谁要它", out)

    def test_unknown_package(self):
        code, _, err = self.run_cli("why", "nope", "web ^1.0")
        self.assertEqual(code, 2)
        self.assertIn("没有 nope", err)

    def test_unsolvable_requirements_fall_back_to_the_explanation(self):
        code, out, _ = self.run_cli("why", "api", "web ^2.0", "http ^1.0")
        self.assertEqual(code, 1)
        self.assertIn("无解", out)


class ShowTests(Runner):
    def test_lists_packages(self):
        code, out, _ = self.run_cli("show")
        self.assertEqual(code, 0)
        self.assertIn("11 个包", out)
        self.assertIn("撤回", out)

    def test_json(self):
        code, out, _ = self.run_cli("show", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload["packages"]), 11)
        http = next(item for item in payload["packages"] if item["name"] == "http")
        self.assertTrue(any(release["yanked"] for release in http["releases"]))


class ScenarioTests(Runner):
    def test_one_scenario(self):
        code, out, _ = self.run_cli("scenario", "basic")
        self.assertEqual(code, 0)
        self.assertIn("### basic", out)

    def test_all_scenarios(self):
        code, out, _ = self.run_cli("scenario", "--all")
        self.assertEqual(code, 1)  # 里面有失败场景
        for name in ("basic", "backtrack", "conflict", "unknown", "chain", "prerelease"):
            self.assertIn(f"### {name}", out)

    def test_unknown_scenario(self):
        code, _, err = self.run_cli("scenario", "nope")
        self.assertEqual(code, 2)
        self.assertIn("没有这个场景", err)

    def test_default_runs_everything(self):
        code, out, _ = self.run_cli("scenario")
        self.assertEqual(code, 1)
        self.assertIn("### basic", out)


class BenchTests(Runner):
    def test_runs_quickly_with_small_settings(self):
        code, out, _ = self.run_cli("bench", "--repeat", "1",
                                    "--packages", "8", "--versions", "2")
        self.assertEqual(code, 0)
        self.assertIn("basic", out)
        self.assertIn("合成", out)
        self.assertIn("ms", out)


class LockTests(Runner):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "ravel.lock")

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)
        os.rmdir(self.directory)

    def test_lock_writes_a_file(self):
        code, out, _ = self.run_cli("lock", "web ^1.0", "-o", self.path)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.path))
        self.assertIn("已写入", out)
        lock = load_lock(self.path)
        self.assertEqual(lock.packages["web"], "1.0.0")

    def test_lock_of_an_unsolvable_request_fails(self):
        code, out, _ = self.run_cli("lock", "web ^2.0", "http ^1.0", "-o", self.path)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.path))

    def test_check_of_a_fresh_lock(self):
        self.run_cli("lock", "web ^1.0", "-o", self.path)
        code, out, _ = self.run_cli("check", "web ^1.0", "-l", self.path)
        self.assertEqual(code, 0)
        self.assertIn("核对", out)
        self.assertIn("仍然成立", out)

    def test_check_flags_a_changed_requirement(self):
        self.run_cli("lock", "web ^1.0", "-o", self.path)
        code, out, _ = self.run_cli("check", "web ^2.0", "-l", self.path)
        self.assertEqual(code, 1)
        self.assertIn("对不上", out)

    def test_check_without_a_lock_file(self):
        code, _, err = self.run_cli("check", "-l", self.path)
        self.assertEqual(code, 2)
        self.assertIn("找不到锁文件", err)

    def test_check_against_another_registry(self):
        self.run_cli("lock", "web ^1.0", "-o", self.path)
        other = os.path.join(self.directory, "other.json")
        with open(other, "w", encoding="utf-8") as stream:
            json.dump({"packages": {"web": {"releases": [{"version": "2.0.0"}]}}}, stream)
        try:
            code, out, _ = self.run_cli("check", "-l", self.path, "--registry", other)
            self.assertEqual(code, 1)
            self.assertIn("没有", out)
        finally:
            os.unlink(other)


class ConsoleEncodingTests(unittest.TestCase):
    """英文 Windows 的控制台是 cp1252，中文一写就 UnicodeEncodeError。"""

    def test_runs_under_a_non_utf8_console(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        env.pop("PYTHONUTF8", None)
        proc = subprocess.run(
            [sys.executable, "-m", "ravel", "solve", "web ^1.0"],
            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stderr = proc.stderr.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertNotIn("UnicodeEncodeError", stderr)
        self.assertIn("web", proc.stdout.decode("utf-8", "replace"))

    def test_explain_also_survives(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        env.pop("PYTHONUTF8", None)
        proc = subprocess.run(
            [sys.executable, "-m", "ravel", "explain", "web ^2.0", "http ^1.0"],
            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr.decode("utf-8", "replace"))
        self.assertIn("结论", proc.stdout.decode("utf-8", "replace"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
