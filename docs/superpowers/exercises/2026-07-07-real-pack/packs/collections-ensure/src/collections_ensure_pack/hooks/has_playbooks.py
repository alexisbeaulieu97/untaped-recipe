from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped_recipe.hook_api import HookHelpers

SKIP_DIRS = {".git", "roles", "group_vars", "host_vars"}


def _is_playbook(doc: object) -> bool:
    if not isinstance(doc, list):
        return False
    return any(
        isinstance(play, dict) and ("hosts" in play or "import_playbook" in play)
        for play in doc
    )


def validate(
    *,
    inputs: dict[str, object],
    target: Path,
    args: dict[str, object],
    helpers: "HookHelpers",
) -> object:
    for path in sorted(target.rglob("*.yml")):
        rel = path.relative_to(target)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        try:
            doc = helpers.load_yaml(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if _is_playbook(doc):
            return helpers.pass_()
    return helpers.fail("no Ansible playbooks found - repo out of scope")
