# Repository Layout Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate all versioned GEAR documentation under `doc/`, keep the repository README and Windows launcher at the root, preserve runtime behavior, verify every example at the strongest level possible without hardware, and push the completed `feat/adb-plugin` branch.

**Architecture:** `framework-dev` remains the Git and technical-project root. Documentation is divided into `doc/cafe`, `doc/contracts`, `doc/development`, and `doc/plugins`; plugin READMEs remain beside plugin code as short entry points. File movement is followed by an automated local-link check, full Framework/plugin regression, fake-hardware integration, example validation, and only then cleanup of duplicate non-Git files outside the repository.

**Tech Stack:** Python 3.11+, pytest, PySide6, Markdown, PowerShell/Windows batch, Git.

**Spec:** `doc/development/superpowers/specs/2026-09-19-repository-layout-design.md`

## Global Constraints

- Keep `C:\dev\GEAR_cafe_docs\framework-dev` as the Git repository root and push `feat/adb-plugin` to `origin`.
- Do not modify Framework contracts, DSL semantics, environment schema, plugin behavior, resource identity, execution exclusivity, or hardware I/O behavior.
- Keep `doc/contracts/` at that exact path because packaging and pytest use it directly.
- Keep `README.md` and `Start-GEAR.cmd` at repository root.
- Move all other versioned technical documentation beneath `doc/`; do not retain compatibility copies.
- Do not access real ADB, COM, relay, or camera hardware during verification.
- Preserve the independent clean checkout `C:\dev\GEAR_cafe_docs\.gear-cafe-publish` unchanged.
- Delete outer duplicate documents only after the repository contains their authoritative content and all verification passes.

## Review Focus

- Markdown image links and links containing `#anchors` must resolve after files move; Task 2 adds a repository-wide local-link test.
- Markdown links shown inside fenced code blocks must not be treated as real links; Task 2 tests and excludes fenced blocks.
- `doc/contracts` must remain installable and importable after Cafe documents move around it; Task 3 runs the editable install/import boundary.
- `Start-GEAR.cmd` must resolve the repository from `%~dp0` when launched from an unrelated working directory; Task 5 runs the existing launcher regression.
- Hardware examples must be loadable without probing real devices; Task 6 performs configuration-only preflight and the guarded four-plugin integration.

---

### Task 1: Freeze and Commit the Existing Functional Baseline

**Files:**
- Modify: `.gitignore`
- Commit: current modified/untracked files under `src/`, `tests/`, `plugins/`, and `docs/`

**Interfaces:**
- Consumes: the current GUI and four plugin implementations already present in the working tree.
- Produces: a clean committed functional baseline that the documentation-only commits can move and reference without mixing behavior changes into path changes.

- [ ] **Step 1: Add repository-wide transient-output ignores**

Add these exact entries to root `.gitignore` if absent:

```gitignore
**/.test-tmp/
**/__pycache__/
```

- [ ] **Step 2: Inspect ignored and untracked files before staging**

Run:

```powershell
git status --short --ignored
```

Expected: source, tests, plugin manifests, examples, docs, and images are visible; `.venv`, `.test-tmp`, `__pycache__`, `runs`, `logs`, and root `environment.yaml` are ignored and will not be staged.

- [ ] **Step 3: Run the existing baseline verification**

Run each in its own process from repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests -q
.\.venv\Scripts\python.exe -B -m pytest plugins/adb/tests --capture=sys -p no:cacheprovider --basetemp=plugins/adb/.test-tmp/layout-baseline -q
.\.venv\Scripts\python.exe -B -m pytest plugins/relay/tests --capture=sys -p no:cacheprovider --basetemp=plugins/relay/.test-tmp/layout-baseline -q
.\.venv\Scripts\python.exe -B -m pytest plugins/console/tests --capture=sys -p no:cacheprovider --basetemp=plugins/console/.test-tmp/layout-baseline -q
.\.venv\Scripts\python.exe -B -m pytest plugins/camera/tests --capture=sys -p no:cacheprovider --basetemp=plugins/camera/.test-tmp/layout-baseline -q
.\.venv\Scripts\python.exe plugins\camera\integration\simulate_board.py plugins\camera\.test-tmp\layout-baseline-integration
```

Expected: 143 Framework, 59 ADB, 155 relay, 92 console, and 31 camera/GUI tests pass; integration prints `FOUR_PLUGIN_OK`.

- [ ] **Step 4: Commit only the functional baseline**

Run:

```powershell
git add .gitignore src tests plugins docs/desktop-status.md
git status --short
git commit -m "feat: add board hardware plugins and workbench GUI"
```

Expected: the implementation and its existing documentation are committed; the repository-layout spec and plan remain in their already dedicated commits or are excluded from this feature commit.

### Task 2: Pin the Final Documentation Structure and Link Behavior

**Files:**
- Create: `tests/test_documentation.py`

**Interfaces:**
- Consumes: repository-relative Markdown paths.
- Produces: `markdown_links(root: Path) -> list[tuple[Path, str]]`, a test-only scanner used to assert that every local Markdown target exists after migration.

- [ ] **Step 1: Write the failing structure and link tests**

Create `tests/test_documentation.py` with:

```python
import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FENCE = re.compile(r"```.*?```", re.DOTALL)


def markdown_links(root):
    found = []
    for document in root.rglob("*.md"):
        if any(part.startswith(".") or part in {".venv", "build", "dist"}
               for part in document.relative_to(root).parts):
            continue
        text = FENCE.sub("", document.read_text(encoding="utf-8"))
        for match in LINK.finditer(text):
            target = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            found.append((document, unquote(target.split("#", 1)[0])))
    return found


def test_all_versioned_documentation_is_under_doc():
    assert not (ROOT / "docs").exists()
    assert not list((ROOT / "plugins").glob("*/docs"))
    assert (ROOT / "doc" / "cafe" / "architecture-v1.md").is_file()
    assert (ROOT / "doc" / "development" / "desktop-status.md").is_file()
    assert (ROOT / "doc" / "plugins" / "adb" / "board-resources.md").is_file()


def test_every_local_markdown_link_resolves():
    failures = []
    for document, target in markdown_links(ROOT):
        if not target:
            continue
        resolved = (document.parent / target).resolve()
        if not resolved.exists():
            failures.append(f"{document.relative_to(ROOT)} -> {target}")
    assert not failures, "Broken local links:\n" + "\n".join(failures)


def test_root_entrypoints_describe_current_project():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert (ROOT / "Start-GEAR.cmd").is_file()
    for required in (
        "doc/README.md", "plugins/adb", "plugins/relay",
        "plugins/console", "plugins/camera", "environment.yaml",
    ):
        assert required in readme


def test_link_scanner_ignores_fenced_examples_and_keeps_real_links(tmp_path):
    (tmp_path / "target.md").write_text("ok", encoding="utf-8")
    (tmp_path / "sample.md").write_text(
        "```markdown\n[example](missing.md)\n```\n[real](target.md#part)\n",
        encoding="utf-8",
    )
    assert markdown_links(tmp_path) == [(tmp_path / "sample.md", "target.md")]
```

- [ ] **Step 2: Run the tests and verify the expected failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_documentation.py -q
```

Expected: the scanner unit test passes; structure and root README assertions fail because `docs/`, plugin `docs/`, and the old README still exist.

- [ ] **Step 3: Commit the failing acceptance tests**

Run:

```powershell
git add tests/test_documentation.py
git commit -m "test: define consolidated documentation layout"
```

### Task 3: Move Cafe and Development Documentation Under `doc/`

**Files:**
- Move: `doc/architecture-v1.md` -> `doc/cafe/architecture-v1.md`
- Move: `doc/contract-decisions.md` -> `doc/cafe/contract-decisions.md`
- Move: `doc/{dsl,environment-model,failure-evidence,framework-runtime,plugin-contract,reporting}` -> `doc/cafe/...`
- Move: `docs/` -> `doc/development/`
- Modify: `doc/README.md`
- Modify: `doc/contracts/README.md`
- Modify: `pyproject.toml` only if validation reveals a stale path; `doc/contracts` itself must not move.

**Interfaces:**
- Consumes: the frozen v1 documents and existing development records.
- Produces: the authoritative `doc/cafe`, `doc/contracts`, and `doc/development` trees.

- [ ] **Step 1: Perform Git-aware moves**

Run from repository root using PowerShell and Git:

```powershell
New-Item -ItemType Directory -Force doc/cafe | Out-Null
git mv doc/architecture-v1.md doc/cafe/architecture-v1.md
git mv doc/contract-decisions.md doc/cafe/contract-decisions.md
git mv doc/dsl doc/cafe/dsl
git mv doc/environment-model doc/cafe/environment-model
git mv doc/failure-evidence doc/cafe/failure-evidence
git mv doc/framework-runtime doc/cafe/framework-runtime
git mv doc/plugin-contract doc/cafe/plugin-contract
git mv doc/reporting doc/cafe/reporting
git mv docs doc/development
```

- [ ] **Step 2: Update the central documentation index**

Rewrite `doc/README.md` so its table links to:

```markdown
| [Architecture v1](cafe/architecture-v1.md) | Boundaries and design principles |
| [Flow DSL v1](cafe/dsl/v1.md) | YAML grammar and execution semantics |
| [Plugin v1](cafe/plugin-contract/v1.md) | Runtime and Workspace contract |
| [Project/Environment v1](cafe/environment-model/v1.md) | Resources and bindings |
| [Runtime v1](cafe/framework-runtime/v1.md) | Framework API and lifecycle |
| [gear_contracts](contracts/README.md) | Exact Python interfaces |
| [Development records](development/) | Implementation status and plans |
| [Plugin records](plugins/) | Hardware plugin implementation notes |
```

- [ ] **Step 3: Update Cafe and contract cross-links**

Apply these path rules throughout `doc/cafe/**/*.md` and `doc/contracts/README.md`:

```text
../contracts/... from old Cafe subdirectories -> ../../contracts/...
../architecture-v1.md from Cafe subdirectories -> ../architecture-v1.md
../plugin-contract/... from doc/contracts -> ../cafe/plugin-contract/...
../framework-runtime/... from doc/contracts -> ../cafe/framework-runtime/...
```

Use `rg -n "architecture-v1|contracts/|plugin-contract|framework-runtime|docs/" doc` to enumerate each remaining occurrence and update it relative to the containing file.

After `docs/` moves, update this plan's `Spec:` header to `doc/development/superpowers/specs/2026-09-19-repository-layout-design.md`; the plan itself will be at `doc/development/superpowers/plans/2026-09-19-repository-layout.md`.

- [ ] **Step 4: Verify package and current link failures**

Run:

```powershell
.\.venv\Scripts\python.exe -c "import gear_contracts; print(gear_contracts.CONTRACT_API)"
.\.venv\Scripts\python.exe -m pytest tests/test_documentation.py -q
```

Expected: contract import prints `gear.plugin/v1`; structure test still fails only because plugin `docs/` remain and root README is not yet rewritten. Any reported Cafe/development broken links are fixed before continuing.

- [ ] **Step 5: Commit Cafe and development moves**

Run:

```powershell
git add doc
git commit -m "docs: consolidate cafe and development records"
```

### Task 4: Move Plugin Documentation and Repair Every Plugin Reference

**Files:**
- Move: `plugins/adb/docs/` -> `doc/plugins/adb/`
- Move: `plugins/relay/docs/` -> `doc/plugins/relay/`
- Move: `plugins/console/docs/` -> `doc/plugins/console/`
- Move: `plugins/camera/docs/` -> `doc/plugins/camera/`
- Modify: `plugins/{adb,relay,console,camera}/README.md`
- Modify: moved Markdown files under `doc/plugins/`

**Interfaces:**
- Consumes: plugin-local detailed records and images.
- Produces: one central plugin documentation tree while preserving plugin READMEs as local entry points.

- [ ] **Step 1: Move plugin documentation**

Run:

```powershell
New-Item -ItemType Directory -Force doc/plugins | Out-Null
git mv plugins/adb/docs doc/plugins/adb
git mv plugins/relay/docs doc/plugins/relay
git mv plugins/console/docs doc/plugins/console
git mv plugins/camera/docs doc/plugins/camera
```

- [ ] **Step 2: Update plugin README links**

Use these exact bases:

```text
plugins/adb/README.md     -> ../../doc/plugins/adb/
plugins/relay/README.md   -> ../../doc/plugins/relay/
plugins/console/README.md -> ../../doc/plugins/console/
plugins/camera/README.md  -> ../../doc/plugins/camera/
```

Replace every former `docs/...` link and image with the corresponding central path. Keep example links under each plugin relative to the plugin README.

- [ ] **Step 3: Update links inside moved plugin documents**

Use these rules and verify each result with the link test:

```text
doc/plugins/<name>/images/... stays images/...
links between plugin records use ../<other-plugin>/...
links back to plugin code or examples use ../../../plugins/<name>/...
links to Cafe contracts use ../../cafe/...
links to root development records use ../../development/...
```

- [ ] **Step 4: Run the documentation tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_documentation.py -q
```

Expected: the structure assertion for `docs/` and plugin `docs/` passes; any remaining failure is limited to root README content or a concrete broken link, which is fixed before commit.

- [ ] **Step 5: Commit centralized plugin records**

Run:

```powershell
git add doc/plugins plugins/*/README.md
git commit -m "docs: centralize plugin documentation"
```

### Task 5: Rewrite Root README and Validate the Double-Click Entry Point

**Files:**
- Modify: `README.md`
- Modify: `Start-GEAR.cmd`
- Modify: `tests/test_desktop.py` only if the launcher-path assertion needs the new documentation path.

**Interfaces:**
- Consumes: current CLI, GUI, plugin manifests, examples, and `doc/README.md`.
- Produces: the project entry page and root Windows double-click launcher.

- [ ] **Step 1: Rewrite `README.md` around the current product**

Use these top-level sections in order:

```markdown
# GEAR
## What is implemented
## Install
## Double-click GUI startup
## Resource configuration
## Test cases and resource references
## CLI
## Plugin development
## Documentation
## Verification
## Hardware validation boundary
```

The README must name and link all four plugins, explain that GUI uses the single root `environment.yaml`, link the complete relay board demo, describe resource IDs in Project/Case files, link `doc/README.md`, and remove the stale sentence claiming COM/camera/relay are unimplemented.

- [ ] **Step 2: Update launcher help text without changing behavior**

Keep the execution line exactly:

```bat
".venv\Scripts\python.exe" -m gear_framework gui --app-dir .
```

Update setup help to install both contracts and GUI/test extras:

```bat
echo   .venv\Scripts\python.exe -m pip install -e ./doc/contracts -e ".[gui,test]"
```

- [ ] **Step 3: Run root-entry and launcher tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_documentation.py tests/test_desktop.py::test_gui_command_launches_without_required_case_or_project -q
```

Then run the existing launcher from a temporary unrelated working directory with the GUI auto-close harness used by `tests/test_desktop.py`; expected behavior is that it loads `<repo>/environment.yaml`, never opens an environment picker, and exits normally.

- [ ] **Step 4: Commit root entry points**

Run:

```powershell
git add README.md Start-GEAR.cmd tests/test_documentation.py tests/test_desktop.py
git commit -m "docs: refresh project entry points"
```

### Task 6: Validate Every Example and the Complete Repository

**Files:**
- Modify: example README/YAML paths only when validation exposes a stale path.
- Modify: documentation links only when the link test names the broken source and target.

**Interfaces:**
- Consumes: final repository structure and all examples.
- Produces: fresh evidence that behavior survived the move and all examples load at their supported validation level.

- [ ] **Step 1: Run all five pytest suites in separate processes**

Run the six commands from Task 1 Step 3 again with `layout-final` basetemp names.

Expected: all 480 tests pass and integration prints `FOUR_PLUGIN_OK`.

- [ ] **Step 2: Execute the pure in-memory bench example**

Run:

```powershell
"yes" | .\.venv\Scripts\gear.exe run --app-dir examples/bench --case examples/bench/case.yaml --project examples/bench/project.yaml --environment examples/bench/environment.yaml
```

Expected: exit code `0`, outcome `PASS`, and a report under `examples/bench/runs/`.

- [ ] **Step 3: Validate hardware examples without device access**

Use the existing fake-hardware test harnesses to load and preflight:

```text
examples/adb/case.yaml with examples/adb/project.yaml and examples/adb/environment.yaml
plugins/console/examples/case.yaml with plugins/console/examples/project.yaml
plugins/relay/examples/case.yaml with plugins/relay/examples/project.yaml
plugins/relay/examples/board-demo/*.case.yaml with its project.yaml and environment.example.yaml
```

Expected: each YAML document parses; every referenced resource is declared; preflight either succeeds with its supplied example bindings or returns only the explicitly documented missing physical binding, without invoking subprocess, WinDLL, serial, or camera boundaries.

- [ ] **Step 4: Run final path and formatting checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_documentation.py -q
.\.venv\Scripts\python.exe -B -m black --check --target-version py311 src tests plugins
git diff --check
rg -n "\bdocs/|plugins/.+/docs|framework-dev" README.md Start-GEAR.cmd doc plugins examples tests pyproject.toml
```

Expected: documentation tests and Black pass; `git diff --check` is clean; `rg` returns only historical prose that explicitly describes the old layout, or no results. Any operational stale path is fixed.

- [ ] **Step 5: Commit verification-driven fixes**

If Step 1-4 required tracked edits, run:

```powershell
git add README.md Start-GEAR.cmd doc plugins examples tests pyproject.toml
git commit -m "test: verify consolidated project layout"
```

If there are no tracked edits, do not create an empty commit.

### Task 7: Clean the Outer Workspace and Push the Branch

**Files:**
- Remove outside Git after verification: `C:\dev\GEAR_cafe_docs\README.md`, `architecture-v1.md`, `contract-decisions.md`, `contracts/`, `dsl/`, `environment-model/`, `failure-evidence/`, `framework-runtime/`, `plugin-contract/`, `reporting/`
- Preserve: `C:\dev\GEAR_cafe_docs\framework-dev`, `C:\dev\GEAR_cafe_docs\.gear-cafe-publish`

**Interfaces:**
- Consumes: verified committed repository content.
- Produces: a tidy outer workspace and the remote `origin/feat/adb-plugin` branch.

- [ ] **Step 1: Verify absolute cleanup targets**

Resolve every candidate path with PowerShell and assert that its parent is exactly `C:\dev\GEAR_cafe_docs`. Assert that neither preserved directory appears in the removal list. Recompare source Markdown/Python files with their repository counterparts; ignore only generated `build`, `*.egg-info`, and `__pycache__` content in outer `contracts`.

- [ ] **Step 2: Remove verified duplicate outer content with native PowerShell**

Use `Remove-Item -LiteralPath` for the three root files and `Remove-Item -LiteralPath ... -Recurse` for the verified duplicate directories. Do not construct commands by passing paths between shells.

- [ ] **Step 3: Confirm final Git state and history**

Run:

```powershell
git status --short
git log --oneline --decorate -8
git diff origin/feat/adb-plugin...HEAD --stat
```

Expected: working tree clean; commits separate functional work, layout tests, documentation moves, root entry updates, and any verification fixes.

- [ ] **Step 4: Push the current branch**

Run:

```powershell
git push -u origin feat/adb-plugin
```

Expected: push succeeds and the upstream branch points to local `HEAD`.

- [ ] **Step 5: Verify remote identity**

Run:

```powershell
git ls-remote --heads origin feat/adb-plugin
git rev-parse HEAD
```

Expected: both hashes are identical.
