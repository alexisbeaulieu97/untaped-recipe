from pathlib import Path

from collections_ensure_pack.hooks.has_playbooks import validate

from untaped_recipe.hook_worker import HookHelpers


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def test_passes_when_playbook_present(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"site.yml": "---\n- hosts: all\n  roles: [common]\n"})

    verdict = validate(inputs={}, target=repo, args={}, helpers=HookHelpers())

    assert verdict["status"] == "pass"


def test_detects_nonstandard_playbook_dir(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"automation/deploy.yml": "---\n- hosts: web\n  tasks: []\n"})

    verdict = validate(inputs={}, target=repo, args={}, helpers=HookHelpers())

    assert verdict["status"] == "pass"


def test_fails_without_playbooks(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        {
            "group_vars/all.yml": "---\nport: 8080\n",
            "roles/common/tasks/main.yml": "---\n- name: task\n  ansible.builtin.ping: {}\n",
        },
    )

    verdict = validate(inputs={}, target=repo, args={}, helpers=HookHelpers())

    assert verdict["status"] == "fail"
    assert "out of scope" in verdict["message"]
