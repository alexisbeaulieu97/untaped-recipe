"""Repository contract for the public orchestration-v1 adoption."""

from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
STORE = ROOT / ".untaped/orchestration"
MIGRATION = ROOT / "docs/orchestration-migration"
STORE_ID = "sto_019f68b6b0bc726fb1312e294c8c752f"
SOURCE_OID = "643303ae0eab942956c48df627582f406ed5ec5b"
SOURCE_PATH = "orchestration/migration-inputs/untaped-recipe/0fd6f81/docs/decisions.md"
SOURCE_SHA = "90f4b10e963b4e8b2027c0dacffd2211c0abc63f2310fdd52ee0106f6d7c1fb4"
ORIGINAL_OID = "0fd6f8164329477f4627ba68987ed56ebea4ccb5"
SOURCE_REF = (
    "url:https://github.com/alexisbeaulieu97/untaped-dev/blob/"
    f"{SOURCE_OID}/{SOURCE_PATH}"
    "?original_repository=alexisbeaulieu97%2Funtaped-recipe"
    f"&original_oid={ORIGINAL_OID}"
    "&original_path=docs%2Fdecisions.md"
    "&original_reachability=local-only"
    f"#sha256:{SOURCE_SHA}"
)
DECISION_IDS = (
    "dec_019f68b6ba06747796ca7bed5fa865bb",
    "dec_019f68b6bb0e75d8a9dd9fd5832740ea",
    "dec_019f68b6bc1873dc82b7356d50c65432",
    "dec_019f68b6bd1f76a4ba94007a915a825d",
    "dec_019f68b6be25775aa4f2af0e5d9c511d",
    "dec_019f68b6bf2a773c9897fbcefab216e9",
    "dec_019f68b6c03173c598351163d53d1cf8",
    "dec_019f68b6c14572bdb2533141a81f87ff",
)
TITLES = (
    "A deterministic file-transformation engine, not a task runner",
    "Truthful preview is the product: plan → preview → confirm → flush",
    "Everything is a pack",
    "Hooks are the extension model: trusted, out-of-process, pure-data wire contract",
    "Declarative recipe, imperative hook — with typed inputs and strict field templating",
    "Safety is two independent layers: code trust up front, runtime integrity at write time",
    "The golden test harness and the anti-DSL guard",
    "The outcome and verdict vocabulary is a deliberately-owned schema",
)
RANGES = (
    "1-8",
    "9-32",
    "33-33",
    "34-55",
    "56-56",
    "57-81",
    "82-82",
    "83-115",
    "116-116",
    "117-152",
    "153-153",
    "154-182",
    "183-183",
    "184-201",
    "202-202",
    "203-221",
)
BYTE_COUNTS = (421, 1408, 1, 1350, 1, 1441, 1, 2073, 1, 2235, 1, 1931, 1, 1144, 1, 1231)
BLOCK_HASHES = (
    "d00ce059d7f6a544128073dfa4c6cc4c818ed9c5705712b045ef2b1dfd6630d8",
    "02142dd8794b97e7978ce634aa485d2b44e55bee340f8cd2c33f51cc255b69a5",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "415618ae98e86e3cd05990426073ff43ca90dc4c00249ec4e427c7863b0130a3",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "ad6ae581ad685cb0926726ac72d85c7d3067d711c1e55eb447b4242ea3c12204",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "a8dea25fbf789d26ebfcbf64dd4d8e977451086215ad41cc9b57a36426ee0df6",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "23ad9337850627ce6c92c883ee2fbdecd7db4a56f8bcbc118e00b5f094af7ff0",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "c36f7dabb1018bd64e6cf0e141363681e7f9c5a36c1c763a0830f9ff9b86d80a",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "bb0d9ebb3978fe1aa555ec328af4a6300594e341c8e158d54a5a6ef007b3a567",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "49c0f82cd12fe03695a8942c96586ddb50f1e10ca4ceef2f08a21f6335c2aee5",
)
BODY_HASHES = (
    "52a0ac0343a0dff5feb06ac8d314a2c880226d99dda2d702c91ca6bafbc1d73e",
    "fa4bcd1ba5c536168f9527b4bf4285d9a435aef5d594de08cb002a66d535301b",
    "003b22b6cb32a2ad4061907570c577c3e22af22156954473f9a4501497466433",
    "97607f2e47a5d2925286446e35bfeb18ee4ebbeab30d779b4cf90bd885d016d2",
    "c7c7f20040d0034b20b97f928c39ec244157700fc67710dc832b7cbca64ad500",
    "d7ae47a6685a1ff3a9813d58a26431730e09507d17b0e84f86099958614af3a9",
    "9a5efa973c8ca97fdd910d5dfa6d263e3cfe24cf6b90d0c69454dd300db73178",
    "216d01e995f92fbabcd9764cfc27ab96ed855e537aa12ce0fa96f0df44145c46",
)


def load_toml(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def parse_item(path: Path) -> tuple[dict[str, object], bytes]:
    raw = path.read_bytes()
    assert raw.startswith(b"+++\n")
    _, frontmatter, body = raw.split(b"+++\n", 2)
    return tomllib.loads(frontmatter.decode()), body


def test_store_is_public_decision_only_and_childless() -> None:
    store = load_toml(STORE / "store.toml")
    assert store["schema"] == "untaped.orchestration.store/v1"
    assert store["id"] == STORE_ID
    assert store["name"] == "untaped-recipe"
    assert store["visibility"] == "public"
    assert store["timezone"] == "UTC"
    assert store["capabilities"] == {"active_tasks": False}
    assert load_toml(STORE / "registry.toml") == {
        "schema": "untaped.orchestration.registry/v1",
        "store_id": STORE_ID,
    }
    assert not list(STORE.glob("tasks/*.md"))


def test_exact_decisions_use_durable_url_evidence_and_opaque_bodies() -> None:
    paths = sorted((STORE / "decisions").glob("*.md"))
    assert len(paths) == 8
    by_id = {frontmatter["id"]: (frontmatter, body) for frontmatter, body in map(parse_item, paths)}
    assert tuple(by_id) == DECISION_IDS
    for decision_id, title, body_hash in zip(DECISION_IDS, TITLES, BODY_HASHES, strict=True):
        frontmatter, body = by_id[decision_id]
        assert frontmatter["schema"] == "untaped.orchestration.decision/v1"
        assert frontmatter["kind"] == "decision"
        assert frontmatter["title"] == title
        assert frontmatter["created_at"] == "2026-07-08T22:30:57.000Z"
        assert frontmatter["evidence"] == [{"relation": "tracked-by", "reference": SOURCE_REF}]
        assert hashlib.sha256(body).hexdigest() == body_hash
    view = (STORE / "views/decisions.md").read_text(encoding="utf-8")
    assert all(decision_id in view for decision_id in DECISION_IDS)
    assert "moderne for files, driven by hooks" not in view


def test_coverage_is_gapless_and_independently_accepted() -> None:
    coverage = load_toml(MIGRATION / "coverage.toml")
    assert coverage["schema"] == "untaped.orchestration.coverage/v1"
    assert coverage["source_repository"] == "alexisbeaulieu97/untaped-dev"
    assert coverage["source_oid"] == SOURCE_OID
    assert coverage["source_path"] == SOURCE_PATH
    assert coverage["source_sha256"] == SOURCE_SHA
    assert coverage["source_bytes"] == 13241
    assert coverage["source_lines"] == 221
    assert coverage["original_repository"] == "alexisbeaulieu97/untaped-recipe"
    assert coverage["original_oid"] == ORIGINAL_OID
    assert coverage["original_path"] == "docs/decisions.md"
    assert coverage["original_sha256"] == SOURCE_SHA
    assert coverage["original_reachability"] == "local-only"
    blocks = coverage["blocks"]
    assert [block["line_range"] for block in blocks] == list(RANGES)
    assert [block["source_bytes"] for block in blocks] == list(BYTE_COUNTS)
    assert [block["block_sha256"] for block in blocks] == list(BLOCK_HASHES)
    assert {block["review_status"] for block in blocks} == {"accepted"}
    assert {block["review_reference"] for block in blocks} == {"review.md"}
    assert all(block["disposition"] and block["destination"] for block in blocks)
    assert "AGENTS.md permanent-invariant authority" in blocks[0]["disposition"]
    assert "docs concept-page ownership" in blocks[0]["disposition"]
    lines = [line for block in blocks for line in range(*_inclusive(block["line_range"]))]
    assert lines == list(range(1, 222))
    review = (MIGRATION / "review.md").read_text(encoding="utf-8")
    assert "**ACCEPT — no Critical, Important, or Minor findings.**" in review
    assert (
        "3cf3df1559893f0a5b0cb3addb4f2216f6fc0e7b..8aef2c3592cd7ac827a9f905f8d7ed11ebb0ada7"
    ) in review
    assert SOURCE_SHA in review


def _inclusive(value: str) -> tuple[int, int]:
    start, end = map(int, value.split("-"))
    return start, end + 1


def test_import_manifest_is_guarded_ordered_and_portable() -> None:
    manifest = load_toml(MIGRATION / "import.toml")
    assert manifest["schema"] == "untaped.orchestration.import/v1"
    assert manifest["target_store_id"] == STORE_ID
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", manifest["expected_store_revision"])
    assert manifest["require_empty_items"] is True
    records = manifest["records"]
    assert len(records) == 8
    assert len({record["frontmatter_file"] for record in records}) == 8
    assert len({record["body_file"] for record in records}) == 8
    assert [record["source_ref"] for record in records] == [SOURCE_REF] * 8
    assert [record["destination"] for record in records] == ["decisions"] * 8
    inputs = [load_toml(MIGRATION / record["frontmatter_file"]) for record in records]
    assert [frontmatter["id"] for frontmatter in inputs] == list(DECISION_IDS)
    assert [frontmatter["evidence"] for frontmatter in inputs] == [
        [{"relation": "tracked-by", "reference": SOURCE_REF}]
    ] * 8


def test_pointer_ownership_agent_ignore_and_workflow_contracts() -> None:
    pointer = (ROOT / "docs/decisions.md").read_text(encoding="utf-8")
    assert "../.untaped/orchestration/views/decisions.md" in pointer
    assert "untaped-orchestration brief --format json" in pointer
    assert "canonical" in pointer and "generated" in pointer
    assert "orchestration-migration" in pointer
    assert "[AGENTS.md](../AGENTS.md)" in pointer
    assert "permanent invariants" in pointer
    assert "[docs/](./)" in pointer
    assert "concept pages" in pointer
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Durable architecture decisions and rationale" in agents
    assert "[docs/decisions.md](./docs/decisions.md)" in agents
    assert "- Decisions:" in agents
    for phrase in (
        "public decision-only",
        "revision guards",
        "--force-current",
        "human-only",
        "tasks are forbidden",
        "check --local",
        "render --check",
    ):
        assert phrase in agents
    ignores = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    assert {
        ".untaped/orchestration/**/.lock",
        ".untaped/orchestration/**/.DS_Store",
        ".untaped/orchestration/**/.*.untaped-tmp-*",
        ".untaped/orchestration/**/*~",
        ".untaped/orchestration/**/*.swp",
        ".untaped/orchestration/**/*.swo",
        ".untaped/orchestration/**/*.tmp",
        ".untaped/orchestration/**/.#*",
        ".untaped/orchestration/**/#*",
    } <= ignores
    workflow = (ROOT / ".github/workflows/orchestration.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5" in workflow
    assert "astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e" in workflow
    assert 'version: "0.11.19"' in workflow
    prefix = "uvx --python 3.14 --from 'untaped-orchestration==0.1.0' "
    commands = re.findall(r"^\s+run: (uvx .+)$", workflow, re.MULTILINE)
    assert commands == [
        f"{prefix}untaped-orchestration check --local",
        f"{prefix}untaped-orchestration fmt --check --local",
        f"{prefix}untaped-orchestration render --check",
    ]
    assert all(
        path in workflow
        for path in (
            ".untaped/orchestration/**",
            ".github/workflows/orchestration.yml",
            ".gitignore",
            "AGENTS.md",
            "CLAUDE.md",
            "docs/decisions.md",
            "docs/orchestration-migration/**",
        )
    )
    assert "uv sync" not in workflow
    assert "PYTHONPATH" not in workflow
    assert "render --check --local" not in workflow
