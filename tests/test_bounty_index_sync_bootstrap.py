import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "bounty_index_sync.yml"


def test_bounty_index_sync_bootstraps_only_when_workflow_changes():
    text = WORKFLOW.read_text()
    assert "push:" in text
    assert "branches:" in text and "- main" in text
    assert "paths:" in text
    assert '- ".github/workflows/bounty_index_sync.yml"' in text
    assert "data/bounty_index.json" not in text.split("paths:", 1)[1].split("schedule:", 1)[0]
    assert "python -m concierge.readme_sync --require-fresh" in text
    assert 'cron: "0 6 * * *"' in text
    assert "workflow_dispatch:" in text
    assert "group: bounty-index-sync" in text
