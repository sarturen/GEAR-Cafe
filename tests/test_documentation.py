import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FENCE_START = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def strip_fenced_blocks(text):
    kept = []
    marker = None
    width = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if marker is None:
            match = FENCE_START.match(line)
            if match:
                token = match.group(1)
                marker = token[0]
                width = len(token)
            else:
                kept.append(line)
        elif stripped and set(stripped) == {marker} and len(stripped) >= width:
            marker = None
            width = 0
    return "".join(kept)


def markdown_links(root):
    found = []
    for document in root.rglob("*.md"):
        if any(
            part.startswith(".") or part in {".venv", "build", "dist"}
            for part in document.relative_to(root).parts
        ):
            continue
        text = strip_fenced_blocks(document.read_text(encoding="utf-8"))
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
        "doc/README.md",
        "plugins/adb",
        "plugins/relay",
        "plugins/console",
        "plugins/camera",
        "environment.yaml",
    ):
        assert required in readme


def test_link_scanner_ignores_fenced_examples_and_keeps_real_links():
    text = strip_fenced_blocks(
        "````markdown\n```markdown\n[example](missing.md)\n```\n````\n"
        "[real](target.md#part)\n"
    )
    assert LINK.findall(text) == ["target.md#part"]
