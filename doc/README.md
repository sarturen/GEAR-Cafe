# GEAR Cafe Documentation

This workspace contains the GEAR architecture and shared interface contracts.
It is organized by topic, with historical discussion retained in handover files.

## Current contract revision

The 2026-09-18 revision incorporates the user's confirmed decisions on long-lived
connections, complete execution exclusivity and independent Plugin development.
All 11 key decisions in the [decision record](./contract-decisions.md) are
confirmed and incorporated. The v1 contracts and interface definitions are frozen
for implementation; this is not a claim that a Framework implementation or real
hardware passed conformance testing.

| Contract | Scope |
|---|---|
| [Architecture v1](./architecture-v1.md) | Boundaries, simplicity constraints, session ownership and development model |
| [Flow DSL v1](./dsl/v1.md) | YAML grammar, temporal semantics, coverage and results |
| [Plugin v1](./plugin-contract/v1.md) | Discovery, long-lived Runtime, Workspace, validation and conformance |
| [Project/Environment v1](./environment-model/v1.md) | Logical resources, physical bindings, slices and archived inputs |
| [Runtime v1](./framework-runtime/v1.md) | Exact application API, lifecycle, threading, events and reports |
| [gear_contracts package](./contracts/README.md) | Importable Python data, protocol, context and error definitions |

The public package contains no Framework implementation, driver or Qt dependency.
Framework and Plugin developers share it and the documents, without importing
each other's internals. Runtime/hardware conformance still requires implementation
tests; a document or successful package import is not proof of device behavior.

## Source of truth

Versioned contracts and the shared package define current behavior and exact
interface names. Their requirements must agree. conclusion.md is a summary;
handover.md is historical rationale, not a second implementation specification.
If a versioned contract exists, it takes precedence over older prose.

GEAR is agent-assisted to build and agent-free to run.

Prefer explicit constraints and direct solutions. Discuss complexity only for
a concrete low-cost/high-benefit improvement or an unavoidable current requirement.
