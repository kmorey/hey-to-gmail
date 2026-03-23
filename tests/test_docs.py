from pathlib import Path


def test_readme_covers_safety_and_auth_requirements():
    text = Path("README.md").read_text()
    assert "--dry-run" in text
    assert "--execute" in text
    assert "gmail.modify" in text
    assert "~/.config/hey-to-gmail/token.json" in text


def test_readme_documents_uv_workflow():
    text = Path("README.md").read_text()
    assert "curl -LsSf https://astral.sh/uv/install.sh | sh" in text
    assert "irm https://astral.sh/uv/install.ps1 | iex" in text
    assert "uv --version" in text
    assert "uv sync --all-extras" in text
    assert "uv run hey-to-gmail" in text
    assert "uv run pytest" in text


def test_uv_lockfile_exists():
    assert Path("uv.lock").exists()


def test_readme_mentions_frozen_sync_for_reproducibility():
    text = Path("README.md").read_text()
    assert "uv sync --frozen --all-extras" in text


def test_primary_docs_do_not_use_pip_install_r_requirements():
    for path in ["README.md", "CONTRIBUTING.md"]:
        if Path(path).exists():
            text = Path(path).read_text()
            assert "pip install -r requirements.txt" not in text


def test_readme_uses_uv_run_for_cli_commands():
    text = Path("README.md").read_text()
    assert "uv run hey-to-gmail import --help" in text
    assert "uv run hey-to-gmail import --mbox ... --gmail-address ... --hey-address ..." in text


def test_workflows_use_uv_commands_if_present():
    workflows_dir = Path(".github/workflows")
    if not workflows_dir.exists():
        return

    for workflow_path in workflows_dir.glob("*.y*ml"):
        text = workflow_path.read_text()
        assert "uv sync --frozen --all-extras" in text
        assert "uv run pytest" in text
        assert "pip install -r requirements.txt" not in text
