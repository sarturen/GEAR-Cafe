# GEAR Cafe Documentation

> Generated from the 2026-09-17 GEAR architecture Cafe.

This directory is organized by **Cafe topic** rather than by conversation.

Each topic may contain:

- `conclusion.md` — the current accepted design / source of truth for that topic.
- `handover.md` — discussion context, rejected alternatives, open questions, and where the next Cafe should resume.

## Current topics

| Topic | Status | Scope |
|---|---|---|
| [DSL](./dsl/conclusion.md) | Active, relatively mature | Test-case grammar and runtime semantics |
| [Environment Model](./environment-model/conclusion.md) | Active | Project logical resources and bench bindings |
| [Plugin Contract](./plugin-contract/conclusion.md) | Active | Resource types, operations, observations, validations |
| [Failure & Evidence](./failure-evidence/conclusion.md) | Partial | Failure semantics, evidence collection, teardown boundary |
| [Reporting](./reporting/conclusion.md) | Partial | Coverage disclosure and environment snapshot |

## Documentation rule

`conclusion.md` is the source of truth for accepted decisions.

`handover.md` preserves *why* those decisions were made, what was rejected, and what remains open.

Do not promote a handover idea into a conclusion unless it has been explicitly accepted in Cafe.

## Cross-cutting architectural invariant

GEAR is **agent-assisted to build, but agent-free to run**.

Agents may help evolve the framework, implement plugins, and author tests. Test runtime itself is deterministic traditional software.
