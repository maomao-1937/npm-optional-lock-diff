"""Check whether direct optional dependencies lost their recorded lockfile paths."""

import json
import os
from pathlib import PureWindowsPath
import re
import stat
import sys


MAX_BYTES = 8 * 1024 * 1024
MAX_PACKAGES = 100_000
MAX_OPTIONAL_EDGES = 50_000
MAX_PATH_LENGTH = 1024
MAX_PATH_SEGMENTS = 64
MAX_PACKAGE_NAME_LENGTH = 214
NAME_SEGMENT = r"[A-Za-z0-9_~-][A-Za-z0-9._~-]*"
PATH_SEGMENT = re.compile(rf"@?{NAME_SEGMENT}")
PACKAGE_NAME = re.compile(rf"(?:@{NAME_SEGMENT}/)?{NAME_SEGMENT}")


class InputError(ValueError):
    pass


def unique_members(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate_key")
        result[key] = value
    return result


def reject_constant(value):
    raise InputError("invalid_json")


def check_windows_path(path):
    windows_path = PureWindowsPath(path)
    if windows_path.drive.startswith("\\\\") or path.replace("\\", "/").startswith("//"):
        raise InputError("not_regular_file")
    is_reserved = getattr(os.path, "isreserved", None)
    for part in windows_path.parts[1 if windows_path.anchor else 0:]:
        reserved = is_reserved(part) if is_reserved else PureWindowsPath(part).is_reserved()
        if ":" in part or reserved:
            raise InputError("not_regular_file")


def read_json(path):
    if os.name == "nt":
        check_windows_path(path)
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise InputError("not_regular_file")
        # Avoid waiting on a FIFO if the path changes between stat and open.
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise InputError("not_regular_file")
            if info.st_size > MAX_BYTES:
                raise InputError("input_too_large")
            data = stream.read(MAX_BYTES + 1)
    except InputError:
        raise
    except (OSError, ValueError):
        raise InputError("input_read_error") from None
    if len(data) > MAX_BYTES:
        raise InputError("input_too_large")
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError("invalid_utf8") from None
    try:
        return json.loads(
            source, object_pairs_hook=unique_members, parse_constant=reject_constant,
        )
    except InputError:
        raise
    except (ValueError, RecursionError):
        raise InputError("invalid_json") from None


def is_package_name(name):
    return len(name) <= MAX_PACKAGE_NAME_LENGTH and PACKAGE_NAME.fullmatch(name) is not None


def is_installation_path(path):
    if len(path) > MAX_PATH_LENGTH or not path.startswith("node_modules/"):
        return False
    segments = path.split("/")
    if len(segments) > MAX_PATH_SEGMENTS:
        return False
    if any(PATH_SEGMENT.fullmatch(segment) is None for segment in segments):
        return False
    # The final node_modules directory must be followed by a complete package name.
    parent_index = -3 if segments[-2].startswith("@") else -2
    if len(segments) < -parent_index or segments[parent_index] != "node_modules":
        return False
    return is_package_name("/".join(segments[parent_index + 1:]))


def read_packages(path):
    lock = read_json(path)
    if not isinstance(lock, dict):
        raise InputError("invalid_json")
    version = lock.get("lockfileVersion")
    if type(version) is not int or version not in (2, 3):
        raise InputError("unsupported_lockfile_version")
    entries = lock.get("packages")
    if not isinstance(entries, dict) or "" not in entries:
        raise InputError("invalid_packages")
    if len(entries) > MAX_PACKAGES:
        raise InputError("too_many_packages")

    packages = {}
    edge_count = 0
    for location, entry in entries.items():
        if location and not is_installation_path(location):
            raise InputError("unsupported_package_path")
        if not isinstance(entry, dict) or type(entry.get("link", False)) is not bool:
            raise InputError("invalid_package_entry")
        if entry.get("link", False):
            raise InputError("unsupported_link")
        if location == "" and "workspaces" in entry:
            raise InputError("unsupported_workspace")
        edges = entry.get("optionalDependencies", {})
        if not isinstance(edges, dict):
            raise InputError("invalid_optional_dependencies")
        edge_count += len(edges)
        if edge_count > MAX_OPTIONAL_EDGES:
            raise InputError("too_many_optional_edges")
        if any(not is_package_name(name) or not isinstance(spec, str) for name, spec in edges.items()):
            raise InputError("invalid_optional_dependencies")
        if any(spec.lstrip().lower().startswith(("workspace:", "link:")) for spec in edges.values()):
            raise InputError("unsupported_local_edge_spec")
        packages[location] = edges
    return packages


def candidate_prefixes(importer):
    """Return nearest-first lookup prefixes within the lockfile root."""
    segments = importer.split("/") if importer else []
    prefixes = []
    for index in range(len(segments) - 1, -1, -1):
        if segments[index] != "node_modules":
            prefixes.append("/".join(segments[:index + 1]) + "/node_modules/")
    prefixes.append("node_modules/")
    return prefixes


def recorded_target(packages, prefixes, name):
    for prefix in prefixes:
        target = prefix + name
        if target in packages:
            return target
    return None


def find_regressions(before, after):
    regressions = []
    for importer in sorted(before.keys() & after.keys()):
        old_edges = before[importer]
        new_edges = after[importer]
        if not old_edges or not new_edges:
            continue
        prefixes = candidate_prefixes(importer)
        for name in sorted(old_edges.keys() & new_edges.keys()):
            if old_edges[name] != new_edges[name]:
                continue
            previous = recorded_target(before, prefixes, name)
            if previous is not None and recorded_target(after, prefixes, name) is None:
                regressions.append({
                    "importer": importer,
                    "dependency": name,
                    "previous_target": previous,
                })
    return regressions


def write_output(data, exit_code):
    remaining = memoryview(data)
    try:
        while remaining:
            written = os.write(1, remaining)
            if written <= 0:
                return 2
            remaining = remaining[written:]
    except OSError:
        return 2
    return exit_code


def emit(report, exit_code):
    payload = json.dumps(report, ensure_ascii=True, separators=(",", ":"))
    return write_output(payload.encode("ascii") + b"\n", exit_code)


def main(args):
    if args in (["--help"], ["-h"]):
        return write_output(b"Usage: python npm_optional_lock_diff.py [--] BEFORE.json AFTER.json\n", 0)
    if args[:1] == ["--"]:
        args = args[1:]
    elif any(arg.startswith("-") for arg in args):
        return emit({"status": "error", "error": {"code": "usage"}}, 2)
    if len(args) != 2:
        return emit({"status": "error", "error": {"code": "usage"}}, 2)

    locks = []
    for side, path in zip(("before", "after"), args):
        try:
            locks.append(read_packages(path))
        except InputError as error:
            return emit({"status": "error", "error": {"code": str(error), "input": side}}, 2)
    regressions = find_regressions(*locks)
    report = {"status": "regressions" if regressions else "pass", "regressions": regressions}
    return emit(report, int(bool(regressions)))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
