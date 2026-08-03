#!/usr/bin/env python3
"""Run the repository test suite with source-only local imports."""

from __future__ import annotations

# ruff: noqa: E402

import sys


def _bootstrap_trusted_sources() -> dict:
    trusted_loader = globals().get("_EVOZEUS_TRUSTED_SOURCE_LOADER")
    if trusted_loader is not None:
        if trusted_loader not in sys.meta_path:
            raise RuntimeError("trusted source loader is not authoritative")
        sys.meta_path.remove(trusted_loader)
        sys.meta_path.insert(0, trusted_loader)
        scripts_dir = trusted_loader.scripts_dir
        return {
            "scripts_dir": scripts_dir,
            "repository_root": scripts_dir.rsplit("/", 1)[0],
            "pycache_prefix": sys.pycache_prefix,
            "loader": trusted_loader,
        }
    posix = __import__("posix")
    cwd = posix.getcwd()
    original_sys_path = tuple(sys.path)

    def lexical_absolute(raw: str) -> str:
        value = raw if raw.startswith("/") else cwd + "/" + raw
        parts: list[str] = []
        for part in value.split("/"):
            if part in {"", "."}:
                continue
            if part == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(part)
        return "/" + "/".join(parts)

    script = lexical_absolute(__file__)
    scripts_dir = script.rsplit("/", 1)[0]
    system_roots = {
        lexical_absolute(sys.base_prefix),
        lexical_absolute(sys.prefix),
    }
    sys.path[:] = [
        item
        for item in original_sys_path
        if any(
            lexical_absolute(item or cwd) == root
            or lexical_absolute(item or cwd).startswith(root + "/")
            for root in system_roots
        )
    ]
    guard_path = scripts_dir + "/evozeus_source_guard.py"
    flags = posix.O_RDONLY | getattr(posix, "O_CLOEXEC", 0)
    nofollow = getattr(posix, "O_NOFOLLOW", 0)
    if nofollow == 0:
        raise RuntimeError("trusted source bootstrap requires O_NOFOLLOW")
    descriptor = posix.open(guard_path, flags | nofollow)
    try:
        metadata = posix.fstat(descriptor)
        if metadata.st_mode & 0o170000 != 0o100000:
            raise RuntimeError("trusted source bootstrap is not a regular file")
        source = b""
        while len(source) < metadata.st_size:
            chunk = posix.read(descriptor, metadata.st_size - len(source))
            if not chunk:
                raise RuntimeError("trusted source bootstrap changed while reading")
            source += chunk
        if posix.read(descriptor, 1):
            raise RuntimeError("trusted source bootstrap grew while reading")
        final_metadata = posix.fstat(descriptor)
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ) != (
            final_metadata.st_dev,
            final_metadata.st_ino,
            final_metadata.st_mode,
            final_metadata.st_size,
            final_metadata.st_mtime_ns,
            final_metadata.st_ctime_ns,
        ):
            raise RuntimeError("trusted source bootstrap changed while reading")
    finally:
        posix.close(descriptor)
    namespace = {"__file__": guard_path, "__name__": "_evozeus_source_guard"}
    exec(compile(source, guard_path, "exec", dont_inherit=True), namespace)
    return namespace["bootstrap"](__file__, original_sys_path)


_TRUSTED_SOURCE_RUNTIME = _bootstrap_trusted_sources()

import pytest


if __name__ == "__main__":
    raise SystemExit(pytest.main(sys.argv[1:]))
