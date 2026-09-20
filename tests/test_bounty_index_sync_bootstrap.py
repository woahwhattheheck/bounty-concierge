import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "bounty_index_sync.yml"

GENERATOR_INPUTS = (
    ".github/workflows/bounty_index_sync.yml",
    "concierge/bounty_index.py",
    "concierge/bounty_index_publish.py",
    "concierge/readme_sync.py",
    "concierge/config.py",
)

GENERATED_OUTPUTS = (
    "data/bounty_index.json",
    "README.md",
)


def test_bounty_index_sync_rebuilds_on_generator_changes_without_output_loop():
    text = WORKFLOW.read_text()
    assert "push:" in text
    assert "branches:" in text and "- main" in text
    assert "paths:" in text

    push_paths = text.split("paths:", 1)[1].split("schedule:", 1)[0]
    for source_path in GENERATOR_INPUTS:
        assert f'- "{source_path}"' in push_paths

    # The workflow writes these files itself. Making either an input trigger
    # would cause the auto-commit to recursively schedule another rebuild.
    for output_path in GENERATED_OUTPUTS:
        assert output_path not in push_paths

    assert "python -m concierge.bounty_index_publish --output data/bounty_index.json" in text
    assert "python -m concierge.readme_sync --require-fresh" in text
    assert 'cron: "0 6 * * *"' in text
    assert "workflow_dispatch:" in text
    assert "group: bounty-index-sync" in text