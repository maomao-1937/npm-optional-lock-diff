import importlib.util
import json
import ntpath
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "npm_optional_lock_diff.py"
MIB = 1024 * 1024


def lock(root=None, packages=None, version=3, **metadata):
    entries = {"": {} if root is None else root}
    if packages is not None:
        entries.update(packages)
    return {"lockfileVersion": version, "packages": entries, **metadata}


class CliTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.before = self.folder / "private-before.json"
        self.after = self.folder / "private-after.json"

    def write(self, path, value):
        if not isinstance(value, bytes):
            value = json.dumps(value).encode("utf-8")
        path.write_bytes(value)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-I", str(CLI), *map(str, args)],
            capture_output=True,
            timeout=10,
        )

    def compare(self, before, after):
        self.write(self.before, before)
        self.write(self.after, after)
        return self.run_cli(self.before, self.after)

    def assert_report(self, result, exit_code, report):
        expected = json.dumps(report, separators=(",", ":")).encode("ascii") + b"\n"
        self.assertEqual(result.returncode, exit_code, result.stderr)
        self.assertEqual(result.stdout, expected)
        self.assertEqual(result.stderr, b"")

    def assert_error(self, result, code, side=None):
        error = {"code": code}
        if side is not None:
            error["input"] = side
        self.assert_report(result, 2, {"status": "error", "error": error})
        self.assertNotIn(os.fsencode(self.folder), result.stdout + result.stderr)

    def test_root_v2_to_v3_ignores_legacy_dependency_tree(self):
        edge = {"optionalDependencies": {"native": ""}}
        before = lock(edge, {"node_modules/native": {}}, version=2)
        after = lock(edge, dependencies={"native": {"version": "1.0.0"}})
        self.assert_report(self.compare(before, after), 1, {
            "status": "regressions",
            "regressions": [{
                "importer": "", "dependency": "native", "previous_target": "node_modules/native",
            }],
        })

    def test_scoped_lookup_uses_nearest_ancestor_and_ignores_unrelated_copy(self):
        importer = "node_modules/host/node_modules/@tools/worker"
        nearest = importer + "/node_modules/@platform/binary"
        hoisted = "node_modules/host/node_modules/@platform/binary"
        unrelated = "node_modules/other/node_modules/@platform/binary"
        declaration = {"optionalDependencies": {"@platform/binary": "^1"}}
        before = lock(packages={unrelated: {}, importer: declaration, hoisted: {}, nearest: {}})
        after = lock(packages={unrelated: {}, importer: declaration})
        self.assert_report(self.compare(before, after), 1, {
            "status": "regressions",
            "regressions": [{
                "importer": importer, "dependency": "@platform/binary", "previous_target": nearest,
            }],
        })

    def test_kept_ancestor_copy_prevents_regression(self):
        importer = "node_modules/host/node_modules/@tools/worker"
        near = importer + "/node_modules/native"
        hoisted = "node_modules/host/node_modules/native"
        declaration = {"optionalDependencies": {"native": "1"}}
        before = lock(packages={importer: declaration, near: {}, hoisted: {}})
        after = lock(packages={importer: declaration, hoisted: {}})
        self.assert_report(self.compare(before, after), 0, {"status": "pass", "regressions": []})

    def test_scope_directory_is_an_ancestor(self):
        importer = "node_modules/@tools/worker"
        target = "node_modules/@tools/node_modules/native"
        declaration = {"optionalDependencies": {"native": "1"}}
        self.assert_report(
            self.compare(lock(packages={importer: declaration, target: {}}),
                         lock(packages={importer: declaration})),
            1,
            {"status": "regressions", "regressions": [
                {"importer": importer, "dependency": "native", "previous_target": target},
            ]},
        )

    def test_skips_candidate_at_ancestor_node_modules_directory(self):
        importer = "node_modules/host/node_modules/worker"
        declaration = {"optionalDependencies": {"native": "1"}}
        wrong = "node_modules/host/node_modules/node_modules/native"
        proper = "node_modules/host/node_modules/native"
        before = lock(packages={importer: declaration, wrong: {}, proper: {}})
        after = lock(packages={importer: declaration, wrong: {}})
        self.assert_report(self.compare(before, after), 1, {
            "status": "regressions",
            "regressions": [
                {"importer": importer, "dependency": "native", "previous_target": proper},
            ],
        })

    def test_reports_each_importer_independently_in_path_then_name_order(self):
        a = "node_modules/a"
        z = "node_modules/z"
        declarations = {"optionalDependencies": {"z-opt": "1", "a-opt": "1"}}
        before = lock(packages={z: declarations, a: declarations,
                               "node_modules/z-opt": {}, "node_modules/a-opt": {}})
        after = lock(packages={z: declarations, a: declarations, z + "/node_modules/a-opt": {}})
        self.assert_report(self.compare(before, after), 1, {
            "status": "regressions",
            "regressions": [
                {"importer": a, "dependency": "a-opt", "previous_target": "node_modules/a-opt"},
                {"importer": a, "dependency": "z-opt", "previous_target": "node_modules/z-opt"},
                {"importer": z, "dependency": "z-opt", "previous_target": "node_modules/z-opt"},
            ],
        })

    def test_ignores_new_removed_and_changed_declarations_and_optional_peer(self):
        edge = {"optionalDependencies": {"removed": "1", "changed": "1", "always-missing": "1"},
                "peerDependencies": {"peer": "1"},
                "peerDependenciesMeta": {"peer": {"optional": True}}}
        before = lock(edge, {"node_modules/removed": {}, "node_modules/changed": {},
                             "node_modules/peer": {},
                             "node_modules/deleted": {"optionalDependencies": {"peer": "1"}}})
        after = lock({"optionalDependencies": {"changed": "2", "always-missing": "1", "new": "1"},
                      "peerDependencies": {"peer": "1"},
                      "peerDependenciesMeta": {"peer": {"optional": True}}})
        self.assert_report(self.compare(before, after), 0, {"status": "pass", "regressions": []})

    def test_versionless_placeholders_are_recorded_and_secrets_stay_out(self):
        registry = "https://registry.example/SECRET"
        edge = {"optionalDependencies": {"native": registry}}
        before = lock(edge, {"node_modules/native": {"resolved": registry, "optional": True}})
        self.assert_report(self.compare(before, lock(edge)), 1, {
            "status": "regressions",
            "regressions": [
                {"importer": "", "dependency": "native", "previous_target": "node_modules/native"},
            ],
        })

    def test_link_shadowing_a_regular_hoist_is_unsupported(self):
        edge = {"optionalDependencies": {"native": "1"}}
        before = lock(packages={"node_modules/importer": edge, "node_modules/native": {}})
        after = lock(packages={"node_modules/importer": edge, "node_modules/native": {},
                               "node_modules/importer/node_modules/native": {
                                   "link": True, "resolved": "private-workspace",
                               }})
        self.assert_error(self.compare(before, after), "unsupported_link", "after")

    def test_link_field_must_be_boolean(self):
        self.assert_error(
            self.compare(lock(packages={"node_modules/local": {"link": "true"}}), lock()),
            "invalid_package_entry", "before",
        )

    def test_rejects_workspace_declaration(self):
        self.assert_error(self.compare(lock(), lock({"workspaces": ["private/*"]})),
                          "unsupported_workspace", "after")

    def test_rejects_workspace_package_path(self):
        self.assert_error(self.compare(lock(packages={"private/workspace": {}}), lock()),
                          "unsupported_package_path", "before")

    def test_rejects_explicit_workspace_and_link_specs(self):
        for spec in ("workspace:*", "link:private/path"):
            with self.subTest(spec=spec):
                after = lock({"optionalDependencies": {"native": spec}}, {"node_modules/native": {}})
                self.assert_error(self.compare(lock(), after), "unsupported_local_edge_spec", "after")

    def test_rejects_absolute_traversal_and_uri_package_paths(self):
        for name in ("/private/path", "node_modules/../private", "node_modules/a//node_modules/b",
                     "node_modules/a\\b", "node_modules/http:private", "node_modules/@scope"):
            with self.subTest(name=name):
                self.assert_error(self.compare(lock(packages={name: {}}), lock()),
                                  "unsupported_package_path", "before")

    def test_rejects_invalid_optional_metadata(self):
        for optional in (None, ["foo"], {"../outside": "1"}, {"foo": 1}):
            with self.subTest(optional=optional):
                self.assert_error(self.compare(lock(), lock({"optionalDependencies": optional})),
                                  "invalid_optional_dependencies", "after")

    def test_rejects_missing_root_and_nonobject_rows(self):
        self.assert_error(self.compare({"lockfileVersion": 3, "packages": {}}, lock()),
                          "invalid_packages", "before")
        self.assert_error(self.compare(lock(), lock(packages={"node_modules/native": None})),
                          "invalid_package_entry", "after")

    def test_rejects_noninteger_and_unsupported_versions(self):
        for version in (1, 4, "3", 3.0, True):
            with self.subTest(version=version):
                self.assert_error(self.compare(lock(), lock(version=version)),
                                  "unsupported_lockfile_version", "after")

    def test_strict_utf8_json(self):
        for data, code in (
            (b'{"private":"\xff"}', "invalid_utf8"),
            (b'\xef\xbb\xbf{}', "invalid_json"),
            (b'{"private":NaN}', "invalid_json"),
            (b'{"private":1}{}', "invalid_json"),
            (b'{"packages":{"SECRET":{},"SECRET":{}}}', "duplicate_key"),
            (b'[' * 2000 + b']' * 2000, "invalid_json"),
        ):
            with self.subTest(code=code, length=len(data)):
                self.assert_error(self.compare(lock(), data), code, "after")

    def test_read_and_usage_errors_keep_local_paths_out(self):
        self.write(self.before, lock())
        self.assert_error(self.run_cli(self.before, self.after), "input_read_error", "after")
        self.assert_error(self.run_cli(self.folder, self.before), "not_regular_file", "before")
        self.assert_error(self.run_cli(self.before), "usage")
        self.assert_error(self.run_cli(self.before, self.after, self.folder), "usage")
        self.assert_error(self.run_cli(self.before, "--" + str(self.after)), "usage")

    def test_rejects_explicit_windows_special_paths_before_filesystem_access(self):
        spec = importlib.util.spec_from_file_location("npm_optional_lock_diff", CLI)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        stat = mock.Mock(side_effect=AssertionError("stat called on special path"))
        open_file = mock.Mock(side_effect=AssertionError("open called on special path"))
        windows_os = SimpleNamespace(name="nt", path=ntpath, stat=stat, open=open_file)
        with mock.patch.object(module, "os", windows_os):
            for path in (
                r"\\unc.invalid\share\package-lock.json",
                "//unc.invalid/share/package-lock.json",
                r"\\.\pipe\package-lock.json",
                r"\\?\UNC\unc.invalid\share\package-lock.json",
                r"\\?\C:\ordinary\package-lock.json",
                r"C:\locks\NUL.json",
                r"C:\AUX\package-lock.json",
                r"C:\locks\package-lock.json:secret",
                "package-lock.json:secret",
            ):
                with self.subTest(path=path):
                    with self.assertRaisesRegex(module.InputError, "^not_regular_file$"):
                        module.read_json(path)
        stat.assert_not_called()
        open_file.assert_not_called()

    def test_output_to_closed_pipe_exits_two_without_traceback(self):
        self.write(self.before, lock())
        self.write(self.after, lock())
        read_end, write_end = os.pipe()
        os.close(read_end)
        try:
            result = subprocess.run(
                [sys.executable, "-I", str(CLI), str(self.before), str(self.after)],
                stdout=write_end, stderr=subprocess.PIPE, timeout=10,
            )
        finally:
            os.close(write_end)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation requires POSIX")
    def test_fifo_is_rejected_without_reading(self):
        self.write(self.before, lock())
        os.mkfifo(self.after)
        self.assert_error(self.run_cli(self.before, self.after), "not_regular_file", "after")

    def test_rejects_oversized_input(self):
        with self.after.open("wb") as stream:
            stream.truncate(8 * MIB + 1)
        self.write(self.before, lock())
        self.assert_error(self.run_cli(self.before, self.after), "input_too_large", "after")

    def test_rejects_excessive_path_depth_and_optional_edge_count(self):
        too_deep = "/".join(["node_modules/pkg"] * 33)
        self.assert_error(self.compare(lock(), lock(packages={too_deep: {}})),
                          "unsupported_package_path", "after")
        many = {f"opt-{index}": "1" for index in range(50_001)}
        self.assert_error(self.compare(lock({"optionalDependencies": many}), lock()),
                          "too_many_optional_edges", "before")


if __name__ == "__main__":
    unittest.main()
