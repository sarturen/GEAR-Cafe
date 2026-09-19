# GEAR Cafe Documentation

This workspace contains the GEAR architecture and shared interface contracts.
It is organized by topic, with historical discussion retained in handover files.

## Current contract revision

The 2026-09-18 revision incorporates the user's confirmed decisions on long-lived
connections, complete execution exclusivity and independent Plugin development.
All 11 key decisions in the [decision record](cafe/contract-decisions.md) are
confirmed and incorporated. The v1 contracts and interface definitions are frozen
for implementation; this is not a claim that a Framework implementation or real
hardware passed conformance testing.

| Contract | Scope |
|---|---|
| [Architecture v1](cafe/architecture-v1.md) | Boundaries and design principles |
| [Flow DSL v1](cafe/dsl/v1.md) | YAML grammar and execution semantics |
| [Plugin v1](cafe/plugin-contract/v1.md) | Runtime and Workspace contract |
| [Project/Environment v1](cafe/environment-model/v1.md) | Resources and bindings |
| [Runtime v1](cafe/framework-runtime/v1.md) | Framework API and lifecycle |
| [gear_contracts](contracts/README.md) | Exact Python interfaces |
| [Development records](development/) | Implementation status and plans |
| [Plugin records](plugins/) | Hardware plugin implementation notes |

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
