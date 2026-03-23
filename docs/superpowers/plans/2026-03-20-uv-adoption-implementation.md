# UV Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standardize this project on uv for dependency sync, lockfile management, and command execution.

**Architecture:** Keep `pyproject.toml` as the package metadata source, add a committed `uv.lock`, and update docs/tests so uv commands are the single primary workflow. Use small doc/test-focused tasks to avoid risk to importer runtime behavior.

**Tech Stack:** Python, uv, pytest, markdown docs

---

## Planned File Structure

- Modify: `README.md` - uv-first install/run/test workflow and prerequisites
- Modify: `tests/test_docs.py` - assertions for uv workflow and lockfile guidance text
- Create: `uv.lock` - pinned dependency lockfile
- Optional modify: CI workflow file(s) if present for `uv sync --frozen --all-extras` and `uv run pytest`

### Task 1: Add uv Prerequisites and Workflow Docs

**Files:**
- Modify: `README.md`
- Test: `tests/test_docs.py`

- [ ] **Step 1: Write the failing test**

```python
def test_readme_documents_uv_workflow():
    text = Path("README.md").read_text()
    assert "uv sync --all-extras" in text
    assert "uv run hey-to-gmail" in text
    assert "uv run pytest" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py::test_readme_documents_uv_workflow -v`
Expected: FAIL if any required uv string is missing

- [ ] **Step 3: Write minimal implementation**

Update README sections:
- uv install commands (Linux/macOS + Windows PowerShell)
- `uv --version` verification step
- `uv sync --all-extras`
- `uv run hey-to-gmail ...`
- `uv run pytest`

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_docs.py::test_readme_documents_uv_workflow -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_docs.py
git commit -m "docs: standardize uv workflow in readme"
```

### Task 2: Add and Validate uv Lockfile

**Files:**
- Modify: `README.md`
- Create: `uv.lock`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write the failing test**

```python
def test_uv_lockfile_exists():
    assert Path("uv.lock").exists()

def test_readme_mentions_frozen_sync_for_reproducibility():
    text = Path("README.md").read_text()
    assert "uv sync --frozen --all-extras" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py -k "uv_lockfile_exists or frozen_sync" -v`
Expected: FAIL if lockfile is absent

- [ ] **Step 3: Write minimal implementation**

Generate lockfile:

```bash
uv lock
```

If uv is unavailable in environment, document the exact command and create lockfile when uv is available before final merge.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_docs.py -k "uv_lockfile_exists or frozen_sync" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add uv.lock tests/test_docs.py
git commit -m "chore: add uv lockfile"
```

### Task 3: CI/Verification Command Alignment and Doc Sweep

**Files:**
- Modify: workflow file(s) under `.github/workflows/*.yml` if present
- Modify: `README.md`
- Modify: `CONTRIBUTING.md` or other primary onboarding docs if present
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write the failing test/check**

Add concrete docs tests such as:

```python
def test_primary_docs_do_not_use_pip_install_r_requirements():
    for path in ["README.md", "CONTRIBUTING.md"]:
        if Path(path).exists():
            text = Path(path).read_text()
            assert "pip install -r requirements.txt" not in text
```

and CI command checks if workflow file exists.

- [ ] **Step 2: Run check to verify it fails**

Run: `uv run pytest tests/test_docs.py -k "pip_install_r_requirements or uv_workflow" -v`
Expected: FAIL if CI/docs still use non-uv commands

- [ ] **Step 3: Write minimal implementation**

Update CI steps to:
- install uv
- run `uv sync --frozen --all-extras`
- run `uv run pytest`

- [ ] **Step 4: Run check to verify it passes**

Run: `uv run pytest tests/test_docs.py -k "pip_install_r_requirements or uv_workflow" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/*.yml
git commit -m "ci: run tests via uv"
```

### Task 4: Lockfile Guardrail and Final Verification

**Files:**
- Modify: `tests/test_docs.py`
- Verify repository state

- [ ] **Step 1: Write failing guardrail test**

```python
def test_uv_lockfile_exists():
    assert Path("uv.lock").exists()
```

Add a mandatory workflow/guardrail note in docs/tests: when `pyproject.toml` dependency metadata changes, `uv lock` must be run and `uv.lock` must be updated in the same change.

- [ ] **Step 2: Run guardrail/docs tests**

Run: `uv run pytest tests/test_docs.py -v`
Expected: PASS

- [ ] **Step 3: Run full suite (uv command)**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 4: Run uv command sanity check**

Run: `uv --version` and `uv sync --frozen --all-extras`
Expected: commands execute successfully

- [ ] **Step 5: Commit final touch-ups**

```bash
git add .
git commit -m "chore: finalize uv packaging workflow"
```

## Notes for Execution

- Keep changes scoped to packaging/docs/testing workflow; do not alter importer runtime behavior.
- If uv is not installed in the execution environment, record exact manual step required (`uv lock`) and leave code/tests ready for immediate completion once uv is installed.
