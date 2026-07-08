from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped_recipe.hook_api import HookHelpers


def transform(
    content: str,
    *,
    inputs: dict[str, object],
    target: Path,
    file: Path,
    args: dict[str, object],
    helpers: HookHelpers,
) -> str:
    wanted = [str(name) for name in inputs.get("collections") or []]
    data = helpers.load_yaml(content)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{file}: expected a mapping at the document root")

    entries = data.get("collections")
    if entries is None:
        entries = []
        data["collections"] = entries
    if not isinstance(entries, list):
        raise ValueError(f"{file}: 'collections' must be a list")

    present = set()
    for entry in entries:
        if isinstance(entry, str):
            present.add(entry)
        elif isinstance(entry, dict) and "name" in entry:
            present.add(str(entry["name"]))

    missing = [name for name in wanted if name not in present]
    if not missing:
        return content

    plain_string_style = bool(entries) and all(isinstance(entry, str) for entry in entries)
    for name in missing:
        entries.append(name if plain_string_style else {"name": name})
    return helpers.dump_yaml(
        data,
        options={
            "explicit_start": True,
            "indent": {"mapping": 2, "sequence": 4, "offset": 2},
        },
    )


# round-trip 1 marker

# round-trip 2 marker

# round-trip 3 marker
