# UV Adoption Design

## Goal

Standardize this project on `uv` for dependency sync, command execution, and lockfile-based reproducibility.

## Scope

- Adopt uv as the default workflow for local development and testing.
- Commit and maintain `uv.lock`.
- Update docs and docs tests to reflect uv-first commands.

Out of scope:

- Rewriting packaging metadata format (keep `pyproject.toml` as current source of truth).
- Large CI pipeline redesign beyond command updates.

## Chosen Approach

Use full uv standardization without changing project architecture:

1. Keep current PEP 621 `pyproject.toml` layout.
2. Generate and commit `uv.lock`.
3. Make README commands uv-first:
   - setup: `uv sync --all-extras`
   - run: `uv run hey-to-gmail ...`
   - test: `uv run pytest ...`

## Lockfile Policy

- Generate/update lockfile with: `uv lock`.
- Local reproducible sync for verification: `uv sync --frozen --all-extras`.
- CI reproducible sync: `uv sync --frozen --all-extras`.
- Guardrail: if `pyproject.toml` changes, `uv.lock` must be updated in the same change.

## Documentation Changes

- Files to update explicitly:
  - `README.md`
  - any onboarding/contributing docs that reference install/test commands (if present)
  - command reference snippets in `README.md`
- Replace pip-first guidance with uv-first guidance in updated docs.
- Acceptance criterion: no active `pip install -r` workflow remains in primary docs unless explicitly labeled legacy.

## Prerequisites

- Python: keep current project requirement from `pyproject.toml`.
- uv installed and available in shell path.
- Installation commands to document:
  - Linux/macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows (PowerShell): `irm https://astral.sh/uv/install.ps1 | iex`
- Verification command to document: `uv --version`.

## Testing/Verification Changes

- Update `tests/test_docs.py` assertions for required uv workflow strings:
  - `uv sync --all-extras`
  - `uv run hey-to-gmail`
  - `uv run pytest`
- Add/verify lockfile presence check in docs/tests if lightweight and stable.
- Verification commands:
  - `uv run pytest tests/test_docs.py -v`
  - `uv run pytest -v`

## Minimal CI Updates

- Install uv in CI environment.
- Replace dependency step with `uv sync --frozen --all-extras`.
- Run tests with `uv run pytest`.

## Success Criteria

- `uv.lock` is present and committed.
- README consistently documents uv-first setup and command execution.
- Docs tests cover uv workflow text.
- Existing functional test suite remains green.
