"""Adaptive offline smoke test; unchanged first-five scope and mutation observation.

At most three serial foreground service processes. Budget pressure alone permits
retry; business failures, timeouts and mutation evidence stop the loop. Metadata
inventories exclude atime. No target imports, model configuration or target writes.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys

from step4_adaptive_support_20261026 import HARD_LIMIT, adaptive_loop, probe

ROOT = Path(__file__).resolve().parents[2]
# Load only our offline table decoder; the target project is never imported.
sys.path.insert(0, str(ROOT / "src"))
MAX_ENTRIES = 20000
WATCH_MASK = 0x00000002 | 0x00000004 | 0x00000008 | 0x00000040 | 0x00000080 | 0x00000100 | 0x00000200 | 0x00000400 | 0x00000800
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def decode(raw):
    def reject(value):
        raise ValueError("non_finite_json")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject)


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def signature(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_nlink]


def inventory(target, watch=None):
    records = {}
    def visit(path, relative):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode) and watch is not None:
            watch(path, relative)
        records[relative] = {"metadata": signature(info)}
        if stat.S_ISLNK(info.st_mode):
            records[relative]["link"] = os.readlink(path)
        if len(records) > MAX_ENTRIES:
            raise RuntimeError("inventory_entry_budget")
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as listing:
                children = sorted(list(listing), key=lambda entry: entry.name)
            for child in children:
                name = child.name if relative == "." else relative + "/" + child.name
                visit(path / child.name, name)
    visit(target, ".")
    return records


def main():
    parser = argparse.ArgumentParser(description="Offline adaptive budget smoke test; at most three calls")
    parser.add_argument("--initial-budget", type=int, default=4096)
    options = parser.parse_args()
    receipt = {"stage": 3, "status": "BLOCKED", "cwd": str(ROOT),
               "distillation_config_supplied": False, "tool_call_count": 0,
               "selection_order": "lexicographic project-relative .py paths, excluding configured and sensitive paths",
               "mutation_observation": "recursive Linux inotify plus full-tree lstat inventories; excludes atime",
               "initial_budget": options.initial_budget, "max_attempts": 3, "attempts": []}
    fd = None
    try:
        if sys.version_info[:3] != (3, 12, 3):
            raise RuntimeError("system_python_version_changed")
        config = decode((ROOT / "config/projects.json").read_bytes())
        project = config["projects"]["fastcode-sample"]
        ceiling = min(HARD_LIMIT, project["return_bytes"])
        if not 1024 <= options.initial_budget <= ceiling:
            raise ValueError("initial_budget_outside_authorized_limits")
        target = Path(project["root"])
        component = Path("/")
        for part in target.parts[1:]:
            component = component / part
            if stat.S_ISLNK(component.lstat().st_mode):
                raise RuntimeError("target_parent_symlink")
        libc = ctypes.CDLL(None, use_errno=True)
        libc.inotify_init1.argtypes = [ctypes.c_int]
        libc.inotify_init1.restype = ctypes.c_int
        libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        libc.inotify_add_watch.restype = ctypes.c_int
        fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init_failed")
        watched = {}
        def add_watch(path, relative):
            descriptor = libc.inotify_add_watch(fd, os.fsencode(path), WATCH_MASK | IN_ONLYDIR | IN_DONT_FOLLOW)
            if descriptor < 0:
                raise OSError(ctypes.get_errno(), "inotify_watch_failed")
            watched[descriptor] = relative
        before = inventory(target, add_watch)
        def allowed(path):
            parts = PurePosixPath(path).parts
            if not any(prefix == "." or path == prefix or path.startswith(prefix + "/") for prefix in project["include"]):
                return False
            if any(path == prefix or path.startswith(prefix + "/") or ("/" not in prefix and prefix in parts)
                   for prefix in project["exclude"]):
                return False
            for part in parts:
                name = part.casefold()
                if (name in {".ssh", ".aws", ".gnupg", "credentials", "secrets"}
                        or name == ".env" or name.startswith(".env.")
                        or name.startswith(("credentials.", "secrets.", "private."))
                        or name.endswith((".pem", ".key", ".p12", ".pfx"))):
                    return False
            return True
        selected = sorted(path for path, item in before.items()
                          if path.endswith(".py") and stat.S_ISREG(item["metadata"][2]) and allowed(path))[:5]
        if len(selected) != 5:
            raise RuntimeError("fewer_than_five_authorized_sources")
        receipt.update(target=str(target), include=selected, max_seconds=5, hard_limit_bytes=ceiling,
                       inventory_entries=len(before), watched_directories=len(watched))
        def call(budget, number):
            attempt = probe(ROOT, selected, budget, number)
            # Same initial baseline and watches cover every attempt and its gaps.
            after = inventory(target)
            events = []
            while True:
                try:
                    raw = os.read(fd, 65536)
                except BlockingIOError:
                    break
                if not raw:
                    raise RuntimeError("inotify_unexpected_eof")
                offset = 0
                while offset < len(raw):
                    descriptor, mask, cookie, length = struct.unpack_from("iIII", raw, offset)
                    name = os.fsdecode(raw[offset + 16:offset + 16 + length].split(b"\0", 1)[0])
                    events.append({"directory": watched.get(descriptor, "unknown"), "name": name, "mask": mask})
                    offset += 16 + length
            changed = sorted(path for path in before.keys() & after.keys() if before[path] != after[path])
            added = sorted(after.keys() - before.keys())
            removed = sorted(before.keys() - after.keys())
            attempt.update(mutation_event_count=len(events), mutation_events=events[:20],
                           added_count=len(added), modified_count=len(changed), removed_count=len(removed),
                           before_inventory_sha256=hashlib.sha256(encode(before)).hexdigest(),
                           after_inventory_sha256=hashlib.sha256(encode(after)).hexdigest())
            if events or changed or added or removed:
                attempt["status"] = "BLOCKED"
                attempt["reasons"].append("target_mutation_or_observation_loss")
            receipt["attempts"].append(attempt)
            receipt["tool_call_count"] = len(receipt["attempts"])
            print(json.dumps({"adaptive_attempt": attempt}, ensure_ascii=False, allow_nan=False), flush=True)
            return attempt
        attempts, baseline, reason = adaptive_loop(call, options.initial_budget, ceiling)
        receipt.update(status="PASS" if baseline is not None else "BLOCKED", attempts=attempts,
                       baseline=baseline, reason=reason)
        print(json.dumps(receipt, ensure_ascii=False, allow_nan=False), flush=True)
        return 0 if baseline is not None else 1
    except Exception as exception:
        receipt.update(status="BLOCKED", exception_type=type(exception).__name__, reason=str(exception))
        print(json.dumps(receipt, ensure_ascii=False, allow_nan=False), flush=True)
        return 1
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
