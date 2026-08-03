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
    nofollow = getattr(posix, "O_NOFOLLOW", 0)
    directory_flag = getattr(posix, "O_DIRECTORY", 0)
    close_on_exec = getattr(posix, "O_CLOEXEC", 0)
    if nofollow == 0 or directory_flag == 0:
        raise RuntimeError("trusted source bootstrap requires no-follow directory traversal")

    entrypoint_parent = posix.open(
        "/",
        posix.O_RDONLY | directory_flag | nofollow | close_on_exec,
    )
    try:
        for component in script.split("/")[1:-1]:
            next_parent = posix.open(
                component,
                posix.O_RDONLY | directory_flag | nofollow | close_on_exec,
                dir_fd=entrypoint_parent,
            )
            posix.close(entrypoint_parent)
            entrypoint_parent = next_parent
        entrypoint_descriptor = posix.open(
            script.rsplit("/", 1)[1],
            posix.O_RDONLY | nofollow | close_on_exec,
            dir_fd=entrypoint_parent,
        )
        try:
            entrypoint_metadata = posix.fstat(entrypoint_descriptor)
            named_entrypoint = posix.stat(
                script.rsplit("/", 1)[1],
                dir_fd=entrypoint_parent,
                follow_symlinks=False,
            )
            if (
                entrypoint_metadata.st_mode & 0o170000 != 0o100000
                or entrypoint_metadata.st_nlink != 1
                or (
                    entrypoint_metadata.st_dev,
                    entrypoint_metadata.st_ino,
                    entrypoint_metadata.st_mode,
                )
                != (
                    named_entrypoint.st_dev,
                    named_entrypoint.st_ino,
                    named_entrypoint.st_mode,
                )
            ):
                raise RuntimeError(
                    "trusted source entrypoint must be one canonical regular file"
                )
        finally:
            posix.close(entrypoint_descriptor)
    except OSError as exc:
        posix.close(entrypoint_parent)
        raise RuntimeError(
            "trusted source entrypoint path contains a symlink or alias"
        ) from exc
    except BaseException:
        posix.close(entrypoint_parent)
        raise
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
    flags = posix.O_RDONLY | close_on_exec
    descriptor = posix.open(
        "evozeus_source_guard.py",
        flags | nofollow,
        dir_fd=entrypoint_parent,
    )
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
        posix.close(entrypoint_parent)
    namespace = {"__file__": guard_path, "__name__": "_evozeus_source_guard"}
    exec(compile(source, guard_path, "exec", dont_inherit=True), namespace)
    return namespace["bootstrap"](__file__, original_sys_path)


_TRUSTED_SOURCE_RUNTIME = _bootstrap_trusted_sources()

import pytest


if __name__ == "__main__":
    raise SystemExit(pytest.main(sys.argv[1:]))
