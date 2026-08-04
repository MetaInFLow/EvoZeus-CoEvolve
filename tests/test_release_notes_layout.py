from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "docs" / "releases"


def test_versioned_release_notes_live_under_docs_releases() -> None:
    assert list(ROOT.glob("release-notes-v*.md")) == []
    assert (NOTES / "README.md").is_file()
    assert list(NOTES.glob("v*.md"))


def test_release_workflow_reads_commit_bound_canonical_release_notes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert re.search(r"RELEASE_NOTES_DIR:\s*docs/releases", workflow)
    assert 'NOTES_SOURCE="${RELEASE_NOTES_DIR}/${REQUESTED_TAG}.md"' in workflow
    assert (
        'git -C candidate show "${TAG_COMMIT}:${NOTES_SOURCE}" '
        '> "${PAYLOAD_DIR}/${NOTES}"'
    ) in workflow
    assert 'test -s "${PAYLOAD_DIR}/${NOTES}"' in workflow
    assert '--notes-file "${PAYLOAD_DIR}/${NOTES}"' in workflow
    assert "GITHUB_REF_NAME" not in workflow
