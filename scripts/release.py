"""Release helpers for the ``untaped-recipe`` package."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import Version

from untaped_recipe._version import PACKAGE_VERSION
from untaped_recipe.hook_api import HOOK_API_VERSION
from untaped_recipe.infrastructure import pack_scaffold

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "untaped-recipe"
IMPORT_NAME = "untaped_recipe"
SDK_PACKAGE_NAME = "untaped"
SDK_REQUIREMENT = "untaped>=3.0.0,<4"


def verify_versions(expected_version: str) -> None:
    """Require package and hook API scaffold version sources to be consistent."""
    root_version = _project_version(ROOT / "pyproject.toml")
    project_requirement, dev_requirement = pack_scaffold.hook_api_requirements(
        package_version=PACKAGE_VERSION,
        hook_api_version=HOOK_API_VERSION,
    )

    checks = [
        ("root pyproject", root_version, expected_version),
        ("PACKAGE_VERSION", PACKAGE_VERSION, expected_version),
        (
            "scaffold requires_hook_api floor",
            pack_scaffold._HOOK_API_PROJECT_REQUIREMENT,
            project_requirement,
        ),
        ("scaffold dev dependency", pack_scaffold._HOOK_API_DEV_REQUIREMENT, dev_requirement),
    ]
    mismatches = [
        (label, actual, expected) for label, actual, expected in checks if actual != expected
    ]
    if mismatches:
        for label, actual, expected in mismatches:
            print(f"{label}: expected {expected}, got {actual}", file=sys.stderr)
        raise SystemExit(1)


def build_package_wheel(out_dir: Path) -> None:
    """Build the package wheel into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(out_dir),
            "--no-sources",
        ],
        cwd=ROOT,
    )


def verify_sdk_published() -> None:
    """Require the SDK dependency to resolve from a package index."""
    command = _uv_dependency_check_command(
        package_name=SDK_PACKAGE_NAME,
        requirement=SDK_REQUIREMENT,
        import_code=(f"import importlib.metadata as m; print(m.version({SDK_PACKAGE_NAME!r}))"),
    )
    completed = _run_uv_check_once(command)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SystemExit(f"{SDK_REQUIREMENT} is not available from the package index\n{detail}")


def wait_published(
    version: str, *, index_url: str | None = None, timeout_seconds: int = 300
) -> None:
    """Poll an index until the published package can be imported."""
    deadline = time.monotonic() + timeout_seconds
    delay = 5
    command = _uv_install_check_command(version, index_url=index_url)
    last_error = ""
    while time.monotonic() < deadline:
        completed = _run_uv_check_once(command)
        if completed.returncode == 0:
            return
        last_error = completed.stderr.strip() or completed.stdout.strip()
        time.sleep(delay)
        delay = min(delay * 2, 30)
    raise SystemExit(f"{PACKAGE_NAME}=={version} was not available before timeout\n{last_error}")


def smoke_new(
    version: str,
    *,
    index_url: str | None = None,
    find_links: Path | None = None,
) -> None:
    """Run real CLI pack/hook scaffolds in a temporary directory outside this workspace."""
    with tempfile.TemporaryDirectory(prefix="untaped-recipe-smoke-") as temp:
        temp_root = Path(temp)
        library = temp_root / "library"
        env = _isolated_uv_env(temp_root)
        env["UNTAPED_CONFIG"] = str(temp_root / "config.yml")
        env["UNTAPED_RECIPE__LIBRARY_ROOT"] = str(library)
        if index_url is not None:
            env["UV_INDEX"] = index_url
            # Release smokes may install the package-under-test from TestPyPI
            # while resolving its dependencies from PyPI.
            env["UV_INDEX_STRATEGY"] = "unsafe-best-match"
        if find_links is not None:
            env["UV_FIND_LINKS"] = str(find_links.resolve())

        command = [
            "uv",
            "run",
            "--no-project",
            "--refresh-package",
            PACKAGE_NAME,
            "--with",
            f"{PACKAGE_NAME}=={version}",
            "untaped-recipe",
        ]
        _run([*command, "new", "pack", "hook_api_smoke"], cwd=temp_root, env=env)
        _run([*command, "new", "hook", "./hook_api_smoke/probe"], cwd=temp_root, env=env)
        lockfile = temp_root / "hook_api_smoke" / "uv.lock"
        lock = lockfile.read_text()
        if PACKAGE_NAME not in lock or version not in lock:
            raise SystemExit(
                f"smoke lockfile did not include {PACKAGE_NAME}=={version}: {lockfile}"
            )


def publish_package(version: str, *, publish_url: str | None = None) -> None:
    """Publish built package distributions from ``dist``."""
    files = [
        path
        for path in sorted((ROOT / "dist").glob(f"untaped_recipe-{version}*"))
        if _is_package_artifact_version(path, version)
    ]
    if not files:
        raise SystemExit(f"no distributions found in dist/ for version {version}")
    command = ["uv", "publish", "--trusted-publishing", "always"]
    if publish_url is not None:
        command.extend(["--publish-url", publish_url])
    command.extend(str(path) for path in files)
    _run(command, cwd=ROOT)


def _is_package_artifact_version(path: Path, version: str) -> bool:
    if path.name.endswith(".whl"):
        try:
            name, parsed_version, _build, _tags = parse_wheel_filename(path.name)
        except InvalidWheelFilename:
            return False
    else:
        try:
            name, parsed_version = parse_sdist_filename(path.name)
        except InvalidSdistFilename:
            return False
    return canonicalize_name(name) == PACKAGE_NAME and parsed_version == Version(version)


def _uv_install_check_command(version: str, *, index_url: str | None) -> list[str]:
    return _uv_dependency_check_command(
        package_name=PACKAGE_NAME,
        requirement=f"{PACKAGE_NAME}=={version}",
        import_code=f"import {IMPORT_NAME}.hook_api as api; print(api.HOOK_API_VERSION)",
        index_url=index_url,
    )


def _uv_dependency_check_command(
    *,
    package_name: str,
    requirement: str,
    import_code: str,
    index_url: str | None = None,
) -> list[str]:
    command = [
        "uv",
        "run",
        "--no-project",
        "--refresh-package",
        package_name,
    ]
    if index_url is not None:
        command.extend(["--index", index_url, "--index-strategy", "unsafe-best-match"])
    command.extend(
        [
            "--with",
            requirement,
            "python",
            "-c",
            import_code,
        ]
    )
    return command


def _run_uv_check_once(command: list[str]) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="untaped-recipe-install-check-") as temp:
        temp_root = Path(temp)
        return subprocess.run(
            command,
            cwd=temp_root,
            env=_isolated_uv_env(temp_root),
            capture_output=True,
            text=True,
            check=False,
        )


def _isolated_uv_env(temp_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    env["UV_CACHE_DIR"] = str(temp_root / "uv-cache")
    env["UV_NO_PROGRESS"] = "1"
    return env


def _project_version(path: Path) -> str:
    return str(tomllib.loads(path.read_text())["project"]["version"])


def _major_minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> None:
    try:
        subprocess.run(command, cwd=cwd, env=env, check=True)
    except FileNotFoundError as exc:
        raise SystemExit(f"command not found: {command[0]}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify-versions")
    verify.add_argument("version")

    build = subparsers.add_parser("build-package-wheel")
    build.add_argument("--out-dir", type=Path, required=True)

    subparsers.add_parser("verify-sdk-published")

    wait = subparsers.add_parser("wait-published")
    wait.add_argument("version")
    wait.add_argument("--index-url")
    wait.add_argument("--timeout-seconds", type=int, default=300)

    smoke = subparsers.add_parser("smoke-new")
    smoke.add_argument("version")
    smoke.add_argument("--index-url")
    smoke.add_argument("--find-links", type=Path)

    publish = subparsers.add_parser("publish-package")
    publish.add_argument("version")
    publish.add_argument("--publish-url")

    args = parser.parse_args(argv)
    if args.command == "verify-versions":
        verify_versions(args.version)
    elif args.command == "build-package-wheel":
        build_package_wheel(args.out_dir)
    elif args.command == "verify-sdk-published":
        verify_sdk_published()
    elif args.command == "wait-published":
        wait_published(
            args.version,
            index_url=args.index_url,
            timeout_seconds=args.timeout_seconds,
        )
    elif args.command == "smoke-new":
        smoke_new(
            args.version,
            index_url=args.index_url,
            find_links=args.find_links,
        )
    elif args.command == "publish-package":
        publish_package(args.version, publish_url=args.publish_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
