from __future__ import annotations

import importlib._bootstrap_external
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import evozeus_source_guard as source_guard


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_MODULES = (
    "evozeus_wrapper.py",
    "evozeus_wrapper_bootstrap.py",
    "evozeus_wrapper_global_hook.py",
    "evozeus_wrapper_lifecycle.py",
    "evozeus_harness_migration.py",
    "evozeus_official_upgrade_verify.py",
    "evozeus_harness_legacy_prompt_adapter.py",
)


def _copy_runtime(tmp_path: Path) -> Path:
    destination = tmp_path / "runtime"

    def ignored(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name == ".git" or name == "__pycache__" or name.endswith(".pyc")
        }

    shutil.copytree(ROOT, destination, ignore=ignored)
    return destination


def _timestamp_pyc(source: Path, destination: Path, marker: Path) -> None:
    payload = f"open({str(marker)!r}, 'w').write('executed')\n"
    code = compile(payload, str(source), "exec")
    metadata = source.stat()
    pyc = importlib._bootstrap_external._code_to_timestamp_pyc(  # type: ignore[attr-defined]
        code,
        int(metadata.st_mtime),
        metadata.st_size,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(pyc)


def _run(*command: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *command],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def test_timestamp_valid_caches_cannot_replace_protected_source_modules(
    tmp_path: Path,
) -> None:
    runtime = _copy_runtime(tmp_path)
    markers: list[Path] = []
    for filename in PROTECTED_MODULES:
        source = runtime / "scripts" / filename
        marker = tmp_path / f"{source.stem}.executed"
        markers.append(marker)
        cache = Path(importlib.util.cache_from_source(str(source)))
        _timestamp_pyc(source, cache, marker)

    probe = tmp_path / "trusted_import_probe.py"
    probe.write_text(
        "def test_all_protected_modules_import_from_source():\n"
        "    from scripts import evozeus_wrapper\n"
        "    from scripts import evozeus_wrapper_lifecycle\n"
        "    from scripts import evozeus_harness_migration\n"
        "    from scripts import evozeus_official_upgrade_verify\n"
        "    from scripts import evozeus_harness_legacy_prompt_adapter\n"
        "    assert evozeus_wrapper and evozeus_wrapper_lifecycle\n"
        "    assert evozeus_harness_migration and evozeus_official_upgrade_verify\n"
        "    assert evozeus_harness_legacy_prompt_adapter\n",
        encoding="utf-8",
    )
    result = _run(
        str(runtime / "scripts/evozeus_test.py"),
        "-q",
        str(probe),
        cwd=ROOT,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert not any(marker.exists() for marker in markers)


def test_top_level_sourceless_pyc_fails_closed_without_execution(
    tmp_path: Path,
) -> None:
    runtime = _copy_runtime(tmp_path)
    source = runtime / "scripts/evozeus_wrapper_lifecycle.py"
    marker = tmp_path / "sourceless-lifecycle.executed"
    _timestamp_pyc(source, source.with_suffix(".pyc"), marker)
    source.unlink()

    result = _run(
        str(runtime / "scripts/evozeus_wrapper.py"),
        "--help",
        cwd=ROOT,
    )

    assert result.returncode != 0
    assert "trusted source is unavailable" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("relative", [".", "scripts"])
def test_repo_symlink_alias_cannot_supply_tempfile_pyc(
    tmp_path: Path,
    relative: str,
) -> None:
    runtime = _copy_runtime(tmp_path)
    poisoned_root = runtime / relative
    marker = tmp_path / f"tempfile-{relative.replace('/', '-')}.executed"
    fake_source = poisoned_root / "tempfile.py"
    fake_source.write_bytes(b"# timestamp authority only\n")
    _timestamp_pyc(fake_source, poisoned_root / "tempfile.pyc", marker)
    fake_source.unlink()
    alias = tmp_path / "runtime-alias"
    alias.symlink_to(poisoned_root, target_is_directory=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(alias)

    result = _run(
        str(runtime / "scripts/evozeus_wrapper.py"),
        "--help",
        cwd=ROOT,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


@pytest.mark.parametrize(
    "entrypoint",
    [
        "evozeus_wrapper.py",
        "evozeus_wrapper_bootstrap.py",
        "evozeus_official_upgrade_verify.py",
        "evozeus_test.py",
    ],
)
@pytest.mark.parametrize("alias_kind", ["parent", "leaf"])
def test_direct_entrypoint_alias_cannot_select_an_adjacent_source_guard(
    tmp_path: Path,
    entrypoint: str,
    alias_kind: str,
) -> None:
    runtime = _copy_runtime(tmp_path)
    if alias_kind == "parent":
        alias_scripts = tmp_path / "parent-alias"
        alias_scripts.symlink_to(runtime / "scripts", target_is_directory=True)
    else:
        alias_scripts = tmp_path / "leaf-alias"
        shutil.copytree(runtime / "scripts", alias_scripts)
        alias_entrypoint = alias_scripts / entrypoint
        alias_entrypoint.unlink()
        alias_entrypoint.symlink_to(runtime / "scripts" / entrypoint)

    result = _run(
        str(alias_scripts / entrypoint),
        "--help",
        cwd=ROOT,
    )

    assert result.returncode != 0
    assert "trusted source" in result.stderr or "Too many levels" in result.stderr


@pytest.mark.parametrize(
    "entrypoint",
    [
        "evozeus_wrapper.py",
        "evozeus_wrapper_bootstrap.py",
        "evozeus_official_upgrade_verify.py",
        "evozeus_test.py",
    ],
)
def test_parent_directory_exchange_fails_closed_without_loading_replacement_sources(
    tmp_path: Path,
    entrypoint: str,
) -> None:
    runtime = _copy_runtime(tmp_path)
    scripts = runtime / "scripts"
    signal = tmp_path / "guard-captured"
    proceed = tmp_path / "continue-bootstrap"
    entrypoint_path = scripts / entrypoint
    source = entrypoint_path.read_text(encoding="utf-8")
    pause = (
        "        signal_fd = posix.open(\n"
        f"            {str(signal)!r},\n"
        "            posix.O_WRONLY | posix.O_CREAT | posix.O_EXCL,\n"
        "            0o600,\n"
        "        )\n"
        "        posix.close(signal_fd)\n"
        "        while True:\n"
        "            try:\n"
        f"                posix.stat({str(proceed)!r})\n"
        "                break\n"
        "            except FileNotFoundError:\n"
        "                __import__('time').sleep(0.01)\n"
    )
    needle = (
        "        exec(compile(source, guard_path, \"exec\", "
        "dont_inherit=True), namespace)\n"
    )
    assert source.count(needle) == 1
    entrypoint_path.write_text(
        source.replace(needle, needle + pause),
        encoding="utf-8",
    )

    process = subprocess.Popen(
        [sys.executable, str(entrypoint_path), "--help"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not signal.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert signal.exists(), "entrypoint did not reach the directory-exchange barrier"

        retained_scripts = runtime / "scripts-retained"
        scripts.rename(retained_scripts)
        shutil.copytree(retained_scripts, scripts)
        markers: list[Path] = []
        for filename in (
            "evozeus_source_guard.py",
            "evozeus_wrapper_lifecycle.py",
            "evozeus_wrapper_global_hook.py",
        ):
            marker = tmp_path / f"replacement-{Path(filename).stem}.executed"
            markers.append(marker)
            scripts.joinpath(filename).write_text(
                f"open({str(marker)!r}, 'w').write('executed')\n"
                "raise SystemExit(91)\n",
                encoding="utf-8",
            )
        proceed.write_bytes(b"continue\n")
        stdout, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode != 0, stdout
    assert "trusted source directory" in stderr
    assert not any(marker.exists() for marker in markers)


def test_source_reader_detects_same_path_race_by_final_metadata_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "trusted.py"
    source.write_bytes(b"VALUE = 'trusted'\n")
    original_read = source_guard.os.read
    raced = False

    def racing_read(descriptor: int, size: int) -> bytes:
        nonlocal raced
        data = original_read(descriptor, size)
        if data and not raced:
            raced = True
            source.write_bytes(b"VALUE = 'hostile'\n")
        return data

    monkeypatch.setattr(source_guard.os, "read", racing_read)

    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    directory_metadata = os.fstat(directory_fd)
    try:
        with pytest.raises(ImportError, match="metadata changed while reading"):
            source_guard._read_regular_source_at(
                directory_fd,
                (directory_metadata.st_dev, directory_metadata.st_ino),
                str(tmp_path),
                source.name,
                str(source),
            )
    finally:
        os.close(directory_fd)

    assert raced is True
