# GEAR Cafe Documentation

> Generated from the 2026-09-17 GEAR architecture Cafe.

This directory is organized by **Cafe topic** rather than by conversation.

## Frozen v1 contracts

The following documents are normative and sufficient to begin independent
Framework, Plugin, and DSL implementation:

| Contract | Scope |
|---|---|
| [GEAR Architecture v1](./architecture-v1.md) | Component boundaries, Framework API, GUI boundary, and independent development model |
| [GEAR Flow DSL Contract v1](./dsl/v1.md) | Canonical YAML grammar, temporal semantics, Preflight, evidence, and results |
| [GEAR Plugin Contract v1](./plugin-contract/v1.md) | Manifest, capabilities, Runtime protocol, Workspace protocol, validation, and conformance |
| [GEAR Project and Environment Contract v1](./environment-model/v1.md) | Logical resources, physical bindings, device identity, Plugin slices, and Run snapshots |

The v1 contracts take precedence over older descriptive text if a conflict is
found. `conclusion.md` remains the shorter architectural explanation and
`handover.md` remains historical discussion context.

Each topic may contain:

- `conclusion.md` — the current accepted design / source of truth for that topic.
- `handover.md` — discussion context, rejected alternatives, open questions, and where the next Cafe should resume.

## Current topics

| Topic | Status | Scope |
|---|---|---|
| [DSL](./dsl/conclusion.md) | v1 frozen | Test-case grammar and runtime semantics |
| [Environment Model](./environment-model/conclusion.md) | v1 frozen | Project logical resources and bench bindings |
| [Plugin Contract](./plugin-contract/conclusion.md) | v1 frozen | Resource types, capabilities, Runtime and GUI Workspace contracts |
| [Failure & Evidence](./failure-evidence/conclusion.md) | Partial | Failure semantics, evidence collection, teardown boundary |
| [Reporting](./reporting/conclusion.md) | Partial | Coverage disclosure and environment snapshot |
| [Framework Runtime](./framework-runtime/conclusion.md) | Active | Runtime layering, Run lifecycle, preflight, results, and archival |

## Documentation rule

For topics with a frozen `v1.md`, that file is the normative source of truth.

Otherwise, `conclusion.md` is the source of truth for accepted decisions.

`handover.md` preserves *why* those decisions were made, what was rejected, and what remains open.

Do not promote a handover idea into a conclusion unless it has been explicitly accepted in Cafe.

## Cross-cutting architectural invariant

GEAR is **agent-assisted to build, but agent-free to run**.

Agents may help evolve the framework, implement plugins, and author tests. Test runtime itself is deterministic traditional software.
