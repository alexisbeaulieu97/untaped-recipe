"""Release helpers for the ``untaped-recipe-hook-api`` contract package."""

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
from untaped_recipe_hook_api import HOOK_API_VERSION

from untaped_recipe.infrastructure import hook_library

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "untaped-recipe-hook-api"
IMPORT_NAME = "untaped_recipe_hook_api"


def verify_versions(expected_version: str) -> None:
    """Require every hook API version source to match ``expected_version``."""
    root_version = _project_version(ROOT / "pyproject.toml")
    hook_api_version = _project_version(ROOT / "packages" / "hook-api" / "pyproject.toml")
    project_requirement, dev_requirement = hook_library.hook_api_requirements(expected_version)

    checks = [
        ("root pyproject", root_version, expected_version),
        ("hook-api pyproject", hook_api_version, expected_version),
        ("HOOK_API_VERSION", HOOK_API_VERSION, expected_version),
        (
            "scaffold requires_hook_api floor",
            hook_library._HOOK_API_PROJECT_REQUIREMENT,
            project_requirement,
        ),
        ("scaffold dev dependency", hook_library._HOOK_API_DEV_REQUIREMENT, dev_requirement),
    ]
    mismatches = [
        (label, actual, expected) for label, actual, expected in checks if actual != expected
    ]
    if mismatches:
        for label, actual, expected in mismatches:
            print(f"{label}: expected {expected}, got {actual}", file=sys.stderr)
        raise SystemExit(1)


def build_hook_api_wheel(out_dir: Path) -> None:
    """Build the hook API wheel into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "uv",
            "build",
            "--package",
            PACKAGE_NAME,
            "--wheel",
            "--out-dir",
            str(out_dir),
            "--no-sources",
        ],
        cwd=ROOT,
    )


def wait_published(
    version: str, *, index_url: str | None = None, timeout_seconds: int = 300
) -> None:
    """Poll an index until the published hook API package can be imported."""
    deadline = time.monotonic() + timeout_seconds
    delay = 5
    command = _uv_install_check_command(version, index_url=index_url)
    last_error = ""
    while time.monotonic() < deadline:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return
        last_error = completed.stderr.strip() or completed.stdout.strip()
        time.sleep(delay)
        delay = min(delay * 2, 30)
    raise SystemExit(f"{PACKAGE_NAME}=={version} was not available before timeout\n{last_error}")


def smoke_hook_init(
    *,
    index_url: str | None = None,
    find_links: Path | None = None,
    no_index: bool = False,
) -> None:
    """Run a real CLI hook scaffold in a temporary directory outside this workspace."""
    with tempfile.TemporaryDirectory(prefix="untaped-recipe-hook-api-smoke-") as temp:
        temp_root = Path(temp)
        library = temp_root / "library"
        env = os.environ.copy()
        env["UNTAPED_CONFIG"] = str(temp_root / "config.yml")
        env["UNTAPED_RECIPE__LIBRARY_ROOT"] = str(library)
        env.setdefault("UV_CACHE_DIR", str(temp_root / "uv-cache"))
        env["UV_NO_PROGRESS"] = "1"
        if index_url is not None:
            env["UV_DEFAULT_INDEX"] = index_url
        if find_links is not None:
            env["UV_FIND_LINKS"] = str(find_links.resolve())
        if no_index:
            # uv lock does not honor UV_NO_INDEX, so point the default index at an empty
            # local directory while still allowing UV_FIND_LINKS to provide the wheel.
            empty_index = temp_root / "empty-index"
            empty_index.mkdir()
            env["UV_DEFAULT_INDEX"] = empty_index.as_uri()

        _run(
            [
                "uv",
                "run",
                "--project",
                str(ROOT),
                "--frozen",
                "untaped-recipe",
                "hook",
                "init",
                "hook_api_smoke",
            ],
            cwd=temp_root,
            env=env,
        )
        lockfile = library / "hooks" / "hook_api_smoke" / "uv.lock"
        if PACKAGE_NAME not in lockfile.read_text():
            raise SystemExit(f"smoke hook lockfile did not include {PACKAGE_NAME}: {lockfile}")


def publish_hook_api(version: str, *, publish_url: str | None = None) -> None:
    """Publish built hook API distributions from ``dist``."""
    files = [
        path
        for path in sorted((ROOT / "dist").glob(f"untaped_recipe_hook_api-{version}*"))
        if _is_hook_api_artifact_version(path, version)
    ]
    if not files:
        raise SystemExit(f"no hook API distributions found in dist/ for version {version}")
    command = ["uv", "publish", "--trusted-publishing", "always"]
    if publish_url is not None:
        command.extend(["--publish-url", publish_url])
    command.extend(str(path) for path in files)
    _run(command, cwd=ROOT)


def _is_hook_api_artifact_version(path: Path, version: str) -> bool:
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
    command = [
        "uv",
        "run",
        "--no-project",
        "--refresh-package",
        PACKAGE_NAME,
    ]
    if index_url is not None:
        command.extend(["--default-index", index_url])
    command.extend(
        [
            "--with",
            f"{PACKAGE_NAME}=={version}",
            "python",
            "-c",
            f"import {IMPORT_NAME}; print({IMPORT_NAME}.HOOK_API_VERSION)",
        ]
    )
    return command


def _project_version(path: Path) -> str:
    return str(tomllib.loads(path.read_text())["project"]["version"])


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

    build = subparsers.add_parser("build-hook-api-wheel")
    build.add_argument("--out-dir", type=Path, required=True)

    wait = subparsers.add_parser("wait-published")
    wait.add_argument("version")
    wait.add_argument("--index-url")
    wait.add_argument("--timeout-seconds", type=int, default=300)

    smoke = subparsers.add_parser("smoke-hook-init")
    smoke.add_argument("--index-url")
    smoke.add_argument("--find-links", type=Path)
    smoke.add_argument("--no-index", action="store_true")

    publish = subparsers.add_parser("publish-hook-api")
    publish.add_argument("version")
    publish.add_argument("--publish-url")

    args = parser.parse_args(argv)
    if args.command == "verify-versions":
        verify_versions(args.version)
    elif args.command == "build-hook-api-wheel":
        build_hook_api_wheel(args.out_dir)
    elif args.command == "wait-published":
        wait_published(
            args.version,
            index_url=args.index_url,
            timeout_seconds=args.timeout_seconds,
        )
    elif args.command == "smoke-hook-init":
        smoke_hook_init(
            index_url=args.index_url,
            find_links=args.find_links,
            no_index=args.no_index,
        )
    elif args.command == "publish-hook-api":
        publish_hook_api(args.version, publish_url=args.publish_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
