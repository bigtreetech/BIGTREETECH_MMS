# Locate and edit ViViD configuration entries in the host's include tree.
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import argparse
from glob import glob, has_magic
import os
from pathlib import Path
import re
import stat
import tempfile

HEADER = re.compile(r"^\s*\[([^]]+)\]\s*(?:[#;].*)?$")
KLIPPER_CONFIG = "bigtreetech-mms/mms/mms.cfg"
LEGACY_CONFIGS = (
    "bigtreetech-mms/mms.cfg",
    "sample-bigtreetech-mms/mms/mms.cfg",
    "sample-bigtreetech-mms/mms.cfg",
)


def read_config(path, kind, managed=()):
    files = {}
    includes = []
    visiting = set()
    visited = set()

    def visit(spelling):
        source = spelling.resolve()
        if source in visiting:
            raise ValueError(f"Recursive include of {spelling}")
        if spelling in visited:
            return
        visiting.add(source)
        visited.add(spelling)
        if source not in files:
            files[source] = source.read_bytes().decode("utf-8").splitlines(keepends=True)
        lines = files[source]
        for number, line in enumerate(lines):
            header = HEADER.fullmatch(line)
            if header is None or not header[1].startswith("include "):
                continue
            spec = header[1][8:].strip()
            if not spec:
                raise ValueError(f"Empty include in {spelling}")
            pattern = spelling.parent / spec
            wildcard = has_magic(str(pattern))
            paths = sorted(Path(name) for name in glob(str(pattern))) if wildcard else [pattern]
            matches = [target.resolve() for target in paths if target.resolve() in managed]
            if matches:
                includes.extend((source, number, target, wildcard, spelling) for target in matches)
            if not paths and kind == "moonraker":
                raise ValueError(f"No files match {spec} in {spelling}")
            for target in paths:
                if target.resolve() not in managed:
                    visit(Path(os.path.abspath(target)))
        visiting.remove(source)

    visit(path)
    return files, includes


def klipper_changes(main, mode, files, includes, current, known_paths):
    changes = {}
    has_current = any(target == current for _, _, target, _, _ in includes)
    active = {target for _, _, target, _, _ in includes}
    old = next((path for path in known_paths if path.resolve() in active), None)

    for path, number, target, wildcard, spelling in includes:
        if mode != "remove" and target == current:
            continue
        if wildcard:
            raise ValueError(f"{path}:{number + 1} matches a ViViD config with a wildcard; "
                             "use a dedicated include before changing this installation")
        replacement = ""
        if mode != "remove" and not has_current:
            include_path = current if path != spelling else os.path.relpath(current, path.parent)
            ending = "\r\n" if files[path][number].endswith("\r\n") else "\n"
            replacement = f"[include {include_path}]{ending}"
            has_current = True
        changes.setdefault(path, {})[number] = replacement

    result = {path: "".join(edits.get(i, line) for i, line in enumerate(files[path]))
              for path, edits in changes.items()}
    if mode == "install" and not has_current:
        result[main] = f"[include {KLIPPER_CONFIG}]\n" + "".join(files[main])
    return result, old


def moonraker_changes(main, mode, files):
    changes = {}
    found = False
    for path, lines in files.items():
        keep = []
        section = False
        for line in lines:
            match = HEADER.fullmatch(line)
            if match is not None:
                section = match[1] == "mms_server"
                found |= section
            if not section:
                keep.append(line)
        if mode == "remove" and len(keep) != len(lines):
            changes[path] = "".join(keep)
    if mode != "remove" and not found:
        changes[main] = ("".join(files[main]) +
                         "\n[mms_server]\nenable_file_preprocessor: True\n"
                         "enable_toolchange_next_pos: True\n")
    return changes


def write_config(path, content):
    fd, name = tempfile.mkstemp(prefix=".vivid-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode("utf-8"))
            os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("klipper", "moonraker"))
    parser.add_argument("mode", choices=("install", "update", "remove"))
    parser.add_argument("config", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    config = args.config.absolute()
    if not config.is_file():
        if args.kind == "moonraker" or args.mode == "remove":
            return
        parser.exit(1, f"Missing Klipper configuration: {config}\n")
    try:
        root = config.resolve()
        old = None
        if args.kind == "klipper":
            known_paths = [config.parent / name for name in (KLIPPER_CONFIG, *LEGACY_CONFIGS)]
            managed = tuple(path.resolve() for path in known_paths)
            files, includes = read_config(config, args.kind, managed)
            changes, old = klipper_changes(root, args.mode, files, includes, managed[0], known_paths)
        else:
            files, _ = read_config(config, args.kind)
            changes = moonraker_changes(root, args.mode, files)

        for path in changes:
            if not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
                raise OSError(f"Cannot edit {path}")
        if args.check:
            if old is not None and old.is_file():
                print(old)
            return
        for path, content in changes.items():
            write_config(path, content)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"ViViD configuration: {exc}\n")


if __name__ == "__main__":
    main()
