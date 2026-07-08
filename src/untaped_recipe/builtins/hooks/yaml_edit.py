"""Built-in YAML transform hook with mapping/list locators."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from pathlib import Path
from typing import cast

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from untaped_recipe.domain.templates import render_template
from untaped_recipe.infrastructure.hook_helpers import HookHelpers

PathSegment = str | Mapping[str, object]


def transform(
    content: str,
    *,
    inputs: dict[str, object],
    target: Path,
    file: Path,
    args: dict[str, object],
    helpers: HookHelpers,
) -> str:
    """Apply one or more YAML edits to a document."""
    del target, file
    data = helpers.load_yaml(content)
    if data is None:
        data = CommentedMap()
    edits = args.get("edits")
    if not isinstance(edits, Sequence) or isinstance(edits, str):
        raise ValueError("yaml_edit requires args.edits as a list")
    unknown_tokens = _unknown_tokens(args.get("unknown_tokens", "error"))
    changed = False
    for edit in edits:
        if not isinstance(edit, Mapping):
            raise ValueError("yaml_edit edit entries must be mappings")
        if _apply_edit(data, edit, inputs, unknown_tokens=unknown_tokens):
            changed = True
    # No edit mutated the document — return the source verbatim so an
    # ensure that finds everything already present is a byte-identical no-op
    # (no reformatting of untouched files).
    if not changed:
        return content
    return helpers.dump_yaml(data)


def _apply_edit(
    data: object,
    edit: Mapping[str, object],
    inputs: dict[str, object],
    *,
    unknown_tokens: str,
) -> bool:
    op = edit.get("op")
    if op not in {"set", "merge", "delete", "ensure"}:
        raise ValueError(f"yaml_edit invalid op: {op!r}")
    path = _path(edit.get("path"))
    if op == "delete":
        _delete(data, path)
        return True
    value = _render_value(edit.get("value"), inputs, unknown_tokens=unknown_tokens)
    if op == "ensure":
        return _ensure(data, path, value, edit.get("match"))
    if op == "set":
        _set(data, path, value)
        return True
    if not isinstance(value, Mapping):
        raise ValueError("yaml_edit merge value must be a mapping")
    target = _resolve(data, path, create=True, final_container=CommentedMap())
    if not isinstance(target, MutableMapping):
        raise ValueError("yaml_edit merge target must be a mapping")
    target.update(value)
    return True


def _path(raw: object) -> list[PathSegment]:
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ValueError("yaml_edit edit.path must be a list")
    return [cast(PathSegment, segment) for segment in raw]


def _unknown_tokens(raw: object) -> str:
    if raw in {"error", "keep"}:
        return raw
    raise ValueError("unknown_tokens must be 'error' or 'keep'")


def _render_value(value: object, inputs: dict[str, object], *, unknown_tokens: str) -> object:
    if isinstance(value, str):
        return render_template(value, inputs, unknown_tokens=unknown_tokens)
    if isinstance(value, list):
        return [_render_value(item, inputs, unknown_tokens=unknown_tokens) for item in value]
    if isinstance(value, dict):
        return {
            key: _render_value(item, inputs, unknown_tokens=unknown_tokens)
            for key, item in value.items()
        }
    return value


def _set(data: object, path: list[PathSegment], value: object) -> None:
    if not path:
        raise ValueError("yaml_edit set path cannot be empty")
    parent = _resolve(data, path[:-1], create=True, final_container=_container_for(path[-1]))
    _assign(parent, path[-1], value, create=True)


def _delete(data: object, path: list[PathSegment]) -> None:
    if not path:
        raise ValueError("yaml_edit delete path cannot be empty")
    parent = _resolve(data, path[:-1], create=False)
    _remove(parent, path[-1])


def _ensure(
    data: object,
    path: list[PathSegment],
    value: object,
    match: object,
) -> bool:
    """Ensure ``value`` is present at ``path`` (list membership or set-if-absent).

    Returns whether the document was mutated. Presence in a list is decided by
    ``match``-key equality for mapping values, whole-mapping equality for
    mapping values without ``match``, and scalar equality for scalar values;
    absent values are appended verbatim. A mapping ``path`` sets only keys the
    target mapping lacks (shallow, set-if-absent). See docs/hooks.md.
    """
    if not path:
        raise ValueError("yaml_edit ensure path cannot be empty")
    match_keys = _ensure_match_keys(value, match)
    segment = path[-1]
    if not isinstance(segment, str):
        raise ValueError("yaml_edit ensure path must end at a mapping key")
    parent = _resolve(data, path[:-1], create=True, final_container=_container_for(segment))
    if not isinstance(parent, MutableMapping):
        raise ValueError(f"yaml_edit cannot ensure key {segment!r} on non-mapping")
    if segment not in parent:
        parent[segment] = _new_terminal(value, match_keys)
        return True
    terminal = parent[segment]
    if isinstance(terminal, MutableSequence):
        return _ensure_in_list(terminal, value, match_keys)
    if isinstance(terminal, MutableMapping):
        return _ensure_in_mapping(terminal, value, match_keys)
    raise ValueError(f"yaml_edit ensure target must be a list or mapping: {segment}")


def _ensure_match_keys(value: object, match: object) -> tuple[str, ...] | None:
    if isinstance(value, list):
        raise ValueError("yaml_edit ensure value must be a scalar or mapping")
    if match is None:
        return None
    if not isinstance(match, Sequence) or isinstance(match, str):
        raise ValueError("yaml_edit ensure match must be a list of strings")
    keys = list(match)
    if any(not isinstance(key, str) for key in keys):
        raise ValueError("yaml_edit ensure match must be a list of strings")
    if not isinstance(value, Mapping):
        raise ValueError("yaml_edit ensure match requires a mapping value")
    for key in keys:
        if key not in value:
            raise ValueError(f"yaml_edit ensure match key not in value: {key}")
    return tuple(cast(list[str], keys))


def _new_terminal(value: object, match_keys: tuple[str, ...] | None) -> object:
    # A mapping value without match at a missing path is set-if-absent
    # (creates the mapping); scalars and match-keyed mappings create a
    # single-entry list.
    if isinstance(value, Mapping) and match_keys is None:
        created = CommentedMap()
        for key, item in value.items():
            created[key] = item
        return created
    seq = CommentedSeq()
    seq.append(value)
    return seq


def _ensure_in_list(
    sequence: MutableSequence[object],
    value: object,
    match_keys: tuple[str, ...] | None,
) -> bool:
    if isinstance(value, Mapping):
        if _mapping_present(sequence, value, match_keys):
            return False
        sequence.append(value)
        return True
    # Scalar value: string entries participate only in scalar-equality
    # matching, so a mapping entry never matches a scalar value.
    if any(not isinstance(item, Mapping) and item == value for item in sequence):
        return False
    sequence.append(value)
    return True


def _mapping_present(
    sequence: MutableSequence[object],
    value: Mapping[str, object],
    match_keys: tuple[str, ...] | None,
) -> bool:
    for item in sequence:
        if not isinstance(item, Mapping):
            # A mapping value never matches a string (or other scalar) entry.
            continue
        if match_keys is None:
            if dict(item) == dict(value):
                return True
        elif all(item.get(key) == value[key] for key in match_keys):
            return True
    return False


def _ensure_in_mapping(
    mapping: MutableMapping[str, object],
    value: object,
    match_keys: tuple[str, ...] | None,
) -> bool:
    if match_keys is not None:
        raise ValueError("yaml_edit ensure match is not valid for a mapping path")
    if not isinstance(value, Mapping):
        raise ValueError("yaml_edit ensure mapping-path value must be a mapping")
    changed = False
    for key, item in value.items():
        if key not in mapping:
            mapping[key] = item
            changed = True
    return changed


def _resolve(
    data: object,
    path: list[PathSegment],
    *,
    create: bool,
    final_container: object | None = None,
) -> object:
    current = data
    for index, segment in enumerate(path):
        next_segment = path[index + 1] if index + 1 < len(path) else None
        default = final_container if index == len(path) - 1 else _container_for(next_segment)
        current = _select(current, segment, create=create, default=default)
    return current


def _select(
    parent: object,
    segment: PathSegment,
    *,
    create: bool,
    default: object | None,
) -> object:
    if isinstance(segment, str):
        if not isinstance(parent, MutableMapping):
            raise ValueError(f"yaml_edit cannot select key {segment!r} from non-mapping")
        if segment not in parent:
            if not create:
                raise ValueError(f"yaml_edit missing key: {segment}")
            parent[segment] = default if default is not None else CommentedMap()
        return parent[segment]
    return _select_list_item(parent, segment)


def _assign(parent: object, segment: PathSegment, value: object, *, create: bool) -> None:
    if isinstance(segment, str):
        if not isinstance(parent, MutableMapping):
            raise ValueError(f"yaml_edit cannot set key {segment!r} on non-mapping")
        if not create and segment not in parent:
            raise ValueError(f"yaml_edit missing key: {segment}")
        parent[segment] = value
        return
    sequence, index = _list_item(parent, segment)
    sequence[index] = value


def _remove(parent: object, segment: PathSegment) -> None:
    if isinstance(segment, str):
        if not isinstance(parent, MutableMapping):
            raise ValueError(f"yaml_edit cannot delete key {segment!r} from non-mapping")
        if segment not in parent:
            raise ValueError(f"yaml_edit missing key: {segment}")
        del parent[segment]
        return
    sequence, index = _list_item(parent, segment)
    del sequence[index]


def _select_list_item(parent: object, segment: Mapping[str, object]) -> object:
    sequence, index = _list_item(parent, segment)
    return sequence[index]


def _list_item(
    parent: object,
    segment: Mapping[str, object],
) -> tuple[MutableSequence[object], int]:
    if not isinstance(parent, MutableSequence):
        raise ValueError("yaml_edit cannot select a list item from a non-list")
    if "index" in segment:
        index = int(cast(int, segment["index"]))
        try:
            parent[index]
        except IndexError as exc:
            raise ValueError(f"yaml_edit list index out of range: {index}") from exc
        return parent, index
    where = segment.get("where")
    if not isinstance(where, Mapping):
        raise ValueError("yaml_edit list locator requires index or where")
    if any(not isinstance(key, str) for key in where):
        raise ValueError("yaml_edit where keys must be strings")
    fields = cast(Mapping[str, object], where)
    for index, item in enumerate(parent):
        if isinstance(item, Mapping) and _matches(item, fields):
            return parent, index
    raise ValueError(f"yaml_edit no list item matched where: {dict(where)!r}")


def _container_for(segment: PathSegment | None) -> object:
    if isinstance(segment, Mapping):
        return CommentedSeq()
    return CommentedMap()


def _matches(item: Mapping[str, object], where: Mapping[str, object]) -> bool:
    return all(item.get(key) == value for key, value in where.items())
