#!/usr/bin/env python3
"""Source-only import guard for EvoZeus trust entrypoints.

Direct entrypoints compile this file from its adjacent ``.py`` bytes before
normal imports begin.  Local EvoZeus modules are then compiled from regular,
non-symlink source files; import machinery is never allowed to select a local
``.pyc`` file.
"""

from __future__ import annotations

import atexit
import importlib.machinery
import os
import stat
import sys
import types
from typing import Any


_MAX_SOURCE_BYTES = 8 * 1024 * 1024


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _verify_directory_fd(
    directory_fd: int,
    expected_identity: tuple[int, int],
    directory_path: str,
) -> None:
    try:
        metadata = os.fstat(directory_fd)
        named_metadata = os.stat(directory_path, follow_symlinks=False)
    except OSError as exc:
        raise ImportError("trusted source directory is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or not stat.S_ISDIR(named_metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != expected_identity
        or (named_metadata.st_dev, named_metadata.st_ino) != expected_identity
    ):
        raise ImportError("trusted source directory identity changed")


def _read_regular_source_at(
    directory_fd: int,
    expected_directory_identity: tuple[int, int],
    directory_path: str,
    filename: str,
    display_path: str,
) -> bytes:
    if not filename or "/" in filename or filename in {".", ".."}:
        raise ImportError("trusted source leaf is invalid")
    _verify_directory_fd(
        directory_fd,
        expected_directory_identity,
        directory_path,
    )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0:
        raise RuntimeError("source-only import requires O_NOFOLLOW support")
    try:
        descriptor = os.open(
            filename,
            flags | nofollow,
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise ImportError(f"trusted source is unavailable: {display_path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ImportError(f"trusted source is not a regular file: {display_path}")
        if metadata.st_size < 1 or metadata.st_size > _MAX_SOURCE_BYTES:
            raise ImportError(f"trusted source size is invalid: {display_path}")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ImportError(
                    f"trusted source changed while reading: {display_path}"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ImportError(f"trusted source grew while reading: {display_path}")
        final_metadata = os.fstat(descriptor)
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
            raise ImportError(
                f"trusted source metadata changed while reading: {display_path}"
            )
        source = b"".join(chunks)
    finally:
        os.close(descriptor)
    _verify_directory_fd(
        directory_fd,
        expected_directory_identity,
        directory_path,
    )
    return source


class _EvoZeusSourceLoader:
    def __init__(
        self,
        scripts_dir: str,
        scripts_dir_fd: int,
        scripts_dir_identity: tuple[int, int],
    ) -> None:
        self.scripts_dir = scripts_dir
        self._scripts_dir_fd = scripts_dir_fd
        self._scripts_dir_identity = scripts_dir_identity

    def verify_directory(self) -> None:
        _verify_directory_fd(
            self._scripts_dir_fd,
            self._scripts_dir_identity,
            self.scripts_dir,
        )

    def close(self) -> None:
        descriptor = self._scripts_dir_fd
        if descriptor < 0:
            return
        self._scripts_dir_fd = -1
        try:
            os.close(descriptor)
        except OSError:
            pass

    @staticmethod
    def _module_leaf(fullname: str) -> str | None:
        if fullname.startswith("scripts.evozeus_"):
            leaf = fullname.removeprefix("scripts.")
        elif fullname.startswith("evozeus_") and "." not in fullname:
            leaf = fullname
        else:
            return None
        if not leaf.replace("_", "").isalnum():
            return None
        return leaf

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: object = None,
    ) -> importlib.machinery.ModuleSpec | None:
        del path, target
        leaf = self._module_leaf(fullname)
        if leaf is None:
            return None
        source = os.path.join(self.scripts_dir, leaf + ".py")
        # Always return a spec for the protected namespace.  Missing ``.py``
        # therefore fails closed and can never fall through to a sourceless pyc.
        spec = importlib.machinery.ModuleSpec(fullname, self, origin=source)
        spec.has_location = True
        return spec

    def create_module(self, spec: object) -> None:
        del spec
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        fullname = module.__spec__.name if module.__spec__ else None
        if not isinstance(fullname, str):
            raise ImportError("trusted source module lacks an origin")
        leaf = self._module_leaf(fullname)
        if leaf is None:
            raise ImportError("trusted source module name is invalid")
        filename = leaf + ".py"
        source_path = os.path.join(self.scripts_dir, filename)
        source = _read_regular_source_at(
            self._scripts_dir_fd,
            self._scripts_dir_identity,
            self.scripts_dir,
            filename,
            source_path,
        )
        code = compile(source, source_path, "exec", dont_inherit=True)
        module.__dict__["_EVOZEUS_TRUSTED_SOURCE_LOADER"] = self
        exec(code, module.__dict__)


def _install_external_empty_cache(repository_root: str) -> str:
    temp_root = os.path.realpath(os.environ.get("TMPDIR") or "/tmp")
    if _inside(temp_root, repository_root):
        raise RuntimeError("bytecode cache root must be outside the repository")
    for _attempt in range(32):
        token = os.urandom(16).hex()
        cache = os.path.join(temp_root, f"evozeus-pycache-{os.getpid()}-{token}")
        try:
            os.mkdir(cache, 0o700)
        except FileExistsError:
            continue
        metadata = os.lstat(cache)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise RuntimeError("bytecode cache directory is not private")
        if os.listdir(cache):
            raise RuntimeError("bytecode cache directory is not empty")
        sys.pycache_prefix = cache
        sys.dont_write_bytecode = True

        def remove_empty_cache() -> None:
            try:
                os.rmdir(cache)
            except OSError:
                pass

        atexit.register(remove_empty_cache)
        return cache
    raise RuntimeError("cannot allocate an external bytecode cache directory")


def bootstrap(
    script_file: str,
    original_sys_path: tuple[str, ...] | None = None,
    *,
    scripts_dir_fd: int,
    scripts_dir_identity: tuple[int, int],
    scripts_dir_path: str,
) -> dict[str, Any]:
    """Sanitize import paths and install the source-only local module loader."""
    del script_file
    if (
        not isinstance(scripts_dir_fd, int)
        or scripts_dir_fd < 0
        or not isinstance(scripts_dir_identity, tuple)
        or len(scripts_dir_identity) != 2
        or not all(isinstance(value, int) for value in scripts_dir_identity)
        or not isinstance(scripts_dir_path, str)
        or not os.path.isabs(scripts_dir_path)
    ):
        raise RuntimeError("trusted source directory capability is invalid")
    try:
        _verify_directory_fd(
            scripts_dir_fd,
            scripts_dir_identity,
            scripts_dir_path,
        )
        retained_scripts_dir_fd = os.dup(scripts_dir_fd)
    except (ImportError, OSError) as exc:
        raise RuntimeError("trusted source directory capability is unavailable") from exc
    scripts_dir = os.path.abspath(scripts_dir_path)
    repository_root = os.path.dirname(scripts_dir)
    cwd = os.path.realpath(os.getcwd())
    unsafe_roots = {
        scripts_dir,
        repository_root,
        os.path.realpath(scripts_dir),
        os.path.realpath(repository_root),
        cwd,
    }
    clean_path: list[str] = []
    try:
        for raw in original_sys_path or tuple(sys.path):
            candidate = os.path.realpath(os.path.abspath(raw or cwd))
            if any(_inside(candidate, root) for root in unsafe_roots):
                continue
            clean_path.append(raw)
        sys.path[:] = clean_path

        for name in tuple(sys.modules):
            if name.startswith("scripts.evozeus_") or (
                name.startswith("evozeus_") and "." not in name
            ):
                raise RuntimeError(
                    "local EvoZeus module was preloaded before trust bootstrap: "
                    f"{name}"
                )

        cache = _install_external_empty_cache(repository_root)
        package = sys.modules.get("scripts")
        if package is None:
            package = types.ModuleType("scripts")
            package.__package__ = "scripts"
            package.__path__ = []
            package.__spec__ = importlib.machinery.ModuleSpec(
                "scripts",
                loader=None,
                is_package=True,
            )
            sys.modules["scripts"] = package
        elif list(getattr(package, "__path__", [])) not in ([], [scripts_dir]):
            raise RuntimeError("unexpected scripts package was preloaded")

        loader = _EvoZeusSourceLoader(
            scripts_dir,
            retained_scripts_dir_fd,
            scripts_dir_identity,
        )
        loader.verify_directory()
        sys.meta_path.insert(0, loader)
        atexit.register(loader.close)
        retained_scripts_dir_fd = -1
        return {
            "scripts_dir": scripts_dir,
            "repository_root": repository_root,
            "pycache_prefix": cache,
            "loader": loader,
        }
    finally:
        if retained_scripts_dir_fd >= 0:
            os.close(retained_scripts_dir_fd)
