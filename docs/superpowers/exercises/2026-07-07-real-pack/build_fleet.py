#!/usr/bin/env python3
"""Build the simulated ansible-collections consumer fleet for the real-pack exercise.

Usage: python3 build_fleet.py <fleet-root>

Deterministic and stdlib-only. Destroys and recreates <fleet-root> so every
scenario starts fresh. Repo roster and VCS states are pinned by the exercise
spec (docs/superpowers/specs/2026-07-07-real-pack-exercise-design.md).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fleet Builder",
    "GIT_AUTHOR_EMAIL": "fleet@example.invalid",
    "GIT_COMMITTER_NAME": "Fleet Builder",
    "GIT_COMMITTER_EMAIL": "fleet@example.invalid",
    "GIT_AUTHOR_DATE": "2026-07-07T12:00:00Z",
    "GIT_COMMITTER_DATE": "2026-07-07T12:00:00Z",
}

SITE_YML = """---
- name: Site entrypoint
  hosts: all
  roles:
    - common
"""

DEPLOY_YML = """---
- name: Deploy application
  hosts: app_servers
  become: true
  tasks:
    - name: Ensure service directory exists
      ansible.builtin.file:
        path: /opt/app
        state: directory
"""

ROLE_MAIN = """---
- name: Install base packages
  ansible.builtin.package:
    name: htop
    state: present
"""

GROUP_VARS = """---
app_port: 8080
app_user: deploy
"""

REQ_BLOCK = """---
collections:
  - name: community.general
    version: ">=7.0.0"
  - name: ansible.posix
"""

REQ_INLINE = """---
collections: [community.general]
"""

REQ_PRESENT = """---
collections:
  - name: community.general
  - name: acme.internal
"""

REQ_ANCHORS = """---
# Managed by platform team -- do not hand-edit pins without a ticket.
_defaults: &pinned
  version: ">=1.0.0"

collections:
  # general utilities, pinned for prod
  - name: community.general
    <<: *pinned
  - name: ansible.posix    # unpinned on purpose
"""


def run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, env={**GIT_ENV, "PATH": "/usr/bin:/bin"})


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def standard_repo(root: Path, req: str | None, playbook_dir: str = "playbooks") -> None:
    write(root, "site.yml", SITE_YML)
    write(root, f"{playbook_dir}/deploy.yml", DEPLOY_YML)
    write(root, "roles/common/tasks/main.yml", ROLE_MAIN)
    write(root, "group_vars/all.yml", GROUP_VARS)
    if req is not None:
        write(root, "requirements.yml", req)


def git_commit_all(root: Path) -> None:
    run(["git", "init", "--quiet", "--initial-branch=main"], root)
    run(["git", "add", "-A"], root)
    run(["git", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "baseline"], root)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: build_fleet.py <fleet-root>")
    fleet = Path(sys.argv[1]).expanduser().resolve()
    if fleet.exists():
        shutil.rmtree(fleet)
    fleet.mkdir(parents=True)

    # Clean git checkouts.
    for name, req in [
        ("app-alpha", REQ_BLOCK),
        ("app-bravo", REQ_INLINE),
        ("app-charlie", REQ_BLOCK),
        ("app-foxtrot", REQ_ANCHORS),
    ]:
        repo = fleet / name
        standard_repo(repo, req)
        git_commit_all(repo)

    # app-delta: no requirements.yml at all; dirty tree.
    delta = fleet / "app-delta"
    standard_repo(delta, req=None)
    git_commit_all(delta)
    (delta / "group_vars/all.yml").write_text(GROUP_VARS + "feature_flag: true\n", encoding="utf-8")

    # app-echo: collection already present; dirty tree.
    echo = fleet / "app-echo"
    standard_repo(echo, REQ_PRESENT)
    git_commit_all(echo)
    (echo / "site.yml").write_text(SITE_YML + "  become: true\n", encoding="utf-8")

    # app-golf: non-UTF-8 file beside normal YAML; untracked-only changes.
    golf = fleet / "app-golf"
    standard_repo(golf, REQ_BLOCK)
    git_commit_all(golf)
    legacy = golf / "docs/legacy.txt"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes("Prise en charge h\xe9rit\xe9e -- voir l'\xe9quipe.\n".encode("latin-1"))

    # infra-hotel: nonstandard playbook dir, NOT a git repo.
    hotel = fleet / "infra-hotel"
    standard_repo(hotel, REQ_BLOCK, playbook_dir="automation")
    (hotel / "site.yml").unlink()  # only nonstandard-location playbooks

    print(f"fleet ready: {fleet}")


if __name__ == "__main__":
    main()
