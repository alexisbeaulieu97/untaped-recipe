# Architecture decisions

Canonical decision state lives in the repository's public orchestration store. Agents
should begin with `untaped-orchestration brief --format json` and use the CLI for any
further reads or guarded mutations.

The committed [decision view](../.untaped/orchestration/views/decisions.md) is generated,
human-readable output; it is not canonical agent input. The retained
[migration proof](orchestration-migration/coverage.toml) records the durable source and
maps every source byte to its disposition.

The permanent invariants remain owned by [AGENTS.md](../AGENTS.md). Behavior facts remain
owned by the concept pages under [docs/](./); the decisions explain why those contracts
are shaped as they are.
