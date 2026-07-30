from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "docs" / "releases"


def test_versioned_release_notes_live_under_docs_releases() -> None:
    assert list(ROOT.glob("release-notes-v*.md")) == []
    assert (NOTES / "README.md").is_file()
    assert list(NOTES.glob("v*.md"))


def test_tag_workflow_resolves_the_canonical_release_notes_path() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert re.search(r"RELEASE_NOTES_DIR:\s*docs/releases", workflow)
    assert '--notes-file "${RELEASE_NOTES_DIR}/${GITHUB_REF_NAME}.md"' in workflow
    assert 'test -s "${RELEASE_NOTES_DIR}/${GITHUB_REF_NAME}.md"' in workflow
