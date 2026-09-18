# GEAR public contract package

This standalone `gear-contracts` distribution contains definitions only:
no execution engine, Qt import, hardware driver or service registry.

- Python: 3.11 or later; runtime dependencies: none.
- Install in GEAR's Python environment: `python -m pip install ./contracts`.
- Import data from `gear_contracts.data`; protocols and exceptions from
  `gear_contracts.api`.
- `data.py` and `api.py` are normative for exact field and method spelling.
  Behavioral rules live in the versioned contracts.

`TypedDict` values are ordinary dictionaries; type annotations do not validate
values at runtime. Framework and Plugins perform the checks assigned by their
contracts. JSON numbers must be finite and receivers do not mutate snapshots.

Each Plugin uses the single installed package, without bundling a private copy.
Manifest API id `gear.plugin/v1` is the compatibility boundary; mismatches are
rejected, not negotiated.

See [Architecture](../architecture-v1.md),
[Plugin](../plugin-contract/v1.md) and
[Runtime](../framework-runtime/v1.md).
This repository supplies contracts, not a functioning Framework or device
Plugins. Import checks do not establish Runtime or hardware conformance.
