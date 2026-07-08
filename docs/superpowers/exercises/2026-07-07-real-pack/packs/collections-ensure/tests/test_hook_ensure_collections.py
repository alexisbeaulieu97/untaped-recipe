from pathlib import Path

from untaped_recipe.hook_worker import HookHelpers

from collections_ensure_pack.hooks.ensure_collections import transform


def _run(content: str, wanted: list[str]) -> str:
    return transform(
        content,
        inputs={"collections": wanted},
        target=Path("."),
        file=Path("requirements.yml"),
        args={},
        helpers=HookHelpers(),
    )


def test_noop_is_byte_identical_when_all_present() -> None:
    content = "---\ncollections:\n  - name: community.general\n"

    assert _run(content, ["community.general"]) == content


def test_appends_missing_collection_as_mapping() -> None:
    content = "---\ncollections:\n  - name: community.general\n"

    result = _run(content, ["community.general", "acme.required"])

    assert "- name: acme.required" in result
    assert result.startswith("---\n")


def test_matches_flow_string_style() -> None:
    result = _run("---\ncollections: [community.general]\n", ["acme.required"])

    assert "collections: [community.general, acme.required]" in result


def test_seeds_collections_key_when_missing() -> None:
    result = _run("---\nroles: []\n", ["acme.required"])

    assert "- name: acme.required" in result


def test_rejects_non_mapping_root() -> None:
    try:
        _run("- just\n- a\n- list\n", ["acme.required"])
    except ValueError as err:
        assert "mapping" in str(err)
    else:
        raise AssertionError("expected ValueError")
