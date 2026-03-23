# Trial Sample Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic trial mode that selects a small curated sample (default 5) so users can preview and optionally import only those messages before a full run.

**Architecture:** Extend CLI/config with trial flags, add deterministic selector logic in importer, and keep normal pipeline behavior unchanged except for index gating in trial mode. Use existing forwarded/dedupe/reporting modules and add only the minimum metadata collection needed for selection preview and report tagging.

**Tech Stack:** Python 3.12+, argparse, pytest, existing importer/checkpoint/reporting modules

---

## Planned File Structure

- Modify: `src/hey_to_gmail/config.py` - add trial settings and validation
- Modify: `src/hey_to_gmail/cli.py` - add flags, trial preview output, preview-only exit flow
- Modify: `src/hey_to_gmail/importer.py` - add trial selector, selected-index gating, shortfall handling
- Modify: `src/hey_to_gmail/reporting.py` - add trial columns to report rows
- Modify: `tests/test_cli.py` - trial flag parsing and preview behavior tests
- Modify: `tests/test_importer_dry_run.py` - deterministic selection, shortfall, preview-action tests
- Modify: `tests/test_importer_execute.py` - selected-index-only execution tests

### Task 1: Add Trial Config Model and Validation

**Files:**
- Modify: `src/hey_to_gmail/config.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_trial_sample_size_validation_rejects_zero(cli_runner):
    result = cli_runner.invoke(["import", "--mbox", "x.mbox", "--gmail-address", "a@gmail.com", "--hey-address", "a@hey.com", "--trial-sample-size", "0"])
    assert result.exit_code != 0

def test_trial_profile_defaults_to_curated_when_sample_size_set(cli_runner, sample_mbox_file):
    result = cli_runner.invoke([
        "import", "--mbox", str(sample_mbox_file), "--gmail-address", "a@gmail.com", "--hey-address", "a@hey.com",
        "--trial-sample-size", "5", "--print-trial-only"
    ])
    assert result.exit_code == 0
    assert "trial profile: curated" in result.stdout.lower()

def test_trial_profile_rejects_invalid_value(cli_runner, sample_mbox_file):
    result = cli_runner.invoke([
        "import", "--mbox", str(sample_mbox_file), "--gmail-address", "a@gmail.com", "--hey-address", "a@hey.com",
        "--trial-sample-size", "5", "--trial-profile", "invalid"
    ])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_trial_sample_size_validation_rejects_zero -v`
Expected: FAIL because trial fields/validation are missing

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass
class TrialConfig:
    enabled: bool
    sample_size: int
    profile: str
    print_only: bool
    allow_short_trial: bool
```

Add validation rules:
- `sample_size <= 0` => error
- `sample_size == 1` => importable-only selection target
- `sample_size >= 2` => require 1 forwarded + `n-1` importable

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py::test_trial_sample_size_validation_rejects_zero -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hey_to_gmail/config.py tests/test_cli.py
git commit -m "feat: add trial config and validation rules"
```

### Task 2: Add CLI Trial Flags and Preview-Only Flow

**Files:**
- Modify: `src/hey_to_gmail/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_cli_accepts_trial_flags(cli_runner, sample_mbox_file):
    result = cli_runner.invoke([
        "import", "--mbox", str(sample_mbox_file), "--gmail-address", "a@gmail.com", "--hey-address", "a@hey.com",
        "--trial-sample-size", "5", "--trial-profile", "curated", "--print-trial-only"
    ])
    assert result.exit_code == 0
    assert "Trial Selection Preview" in result.stdout

def test_cli_preview_includes_required_columns(cli_runner, sample_mbox_file):
    result = cli_runner.invoke([
        "import", "--mbox", str(sample_mbox_file), "--gmail-address", "a@gmail.com", "--hey-address", "a@hey.com",
        "--trial-sample-size", "5", "--trial-profile", "curated", "--print-trial-only"
    ])
    assert "index" in result.stdout
    assert "date" in result.stdout
    assert "from" in result.stdout
    assert "subject" in result.stdout
    assert "expected action" in result.stdout
    assert "attachment hint" in result.stdout

def test_print_trial_only_exits_before_processing(cli_runner, sample_mbox_file):
    result = cli_runner.invoke([
        "import", "--mbox", str(sample_mbox_file), "--gmail-address", "a@gmail.com", "--hey-address", "a@hey.com",
        "--trial-sample-size", "5", "--print-trial-only", "--execute"
    ])
    assert result.exit_code == 0
    assert "Trial Selection Preview" in result.stdout
    assert "Import completed" not in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_cli_accepts_trial_flags -v`
Expected: FAIL because flags/preview are missing

- [ ] **Step 3: Write minimal implementation**

Add CLI flags:
- `--trial-sample-size`
- `--trial-profile` (currently `curated`)
- `--print-trial-only`
- `--allow-short-trial`

Add preview-only exit path after printing selected sample table.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py::test_cli_accepts_trial_flags -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hey_to_gmail/cli.py tests/test_cli.py
git commit -m "feat: add trial cli flags and preview-only mode"
```

### Task 3: Implement Deterministic Curated Trial Selector

**Files:**
- Modify: `src/hey_to_gmail/importer.py`
- Modify: `tests/test_importer_dry_run.py`

- [ ] **Step 1: Write the failing test**

```python
def test_trial_selection_is_deterministic(sample_mbox_file, importer_factory):
    first = importer_factory().select_trial_indices(sample_mbox_file, sample_size=5)
    second = importer_factory().select_trial_indices(sample_mbox_file, sample_size=5)
    assert first == second

def test_trial_selection_n5_targets_one_forwarded_and_four_importable(...):
    ...

def test_trial_selector_excludes_terminal_local_state(...):
    # seed local state as imported/skipped_duplicate and assert excluded indices
    ...

def test_trial_selector_errors_when_forwarded_slot_missing_for_n_ge_2(...):
    # request n>=2 from fixture without forwarded candidates
    # assert error includes forwarded-slot shortfall reason
    ...

def test_trial_selector_allow_short_trial_when_forwarded_slot_missing(...):
    # same fixture with allow_short_trial=True
    # assert shortened output and warning summary
    ...

def test_trial_selector_excludes_unparseable_raw_messages(...):
    # malformed raw-bytes candidates are excluded from precheck-importable pools
    ...

def test_attachment_heuristic_selection(...):
    ...

def test_gmail_origin_heuristic_selection(...):
    ...

def test_plain_text_dominant_heuristic_selection(...):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_importer_dry_run.py::test_trial_selection_is_deterministic -v`
Expected: FAIL because selector is missing

- [ ] **Step 3: Write minimal implementation**

Implement deterministic selector with explicit order:
1. gather candidates in ascending index
2. pick first forwarded (if required)
3. fill importable slots by priority: attachment -> gmail-origin -> plain-text -> fallback
4. dedupe indices and finalize in ascending index

Include shortfall error path and `--allow-short-trial` override.
Enforce precheck exclusion of locally terminal states (`imported`, `skipped_duplicate`) before category assignment.
Exclude messages without parseable raw bytes from precheck-importable pools.
For `n>=2`, emit explicit forwarded-slot shortfall reason if no forwarded candidate exists.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_importer_dry_run.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hey_to_gmail/importer.py tests/test_importer_dry_run.py
git commit -m "feat: add deterministic curated trial sample selector"
```

### Task 4: Gate Pipeline Execution to Trial Selection

**Files:**
- Modify: `src/hey_to_gmail/importer.py`
- Modify: `tests/test_importer_execute.py`

- [ ] **Step 1: Write the failing test**

```python
def test_execute_trial_processes_only_selected_indices(mocked_gmail_client, sample_mbox_file):
    result = run_import(..., trial_sample_size=5, execute=True)
    assert result.total_processed == 5

def test_execute_trial_uses_standard_label_behavior_not_trial_specific(...):
    # assert trial run uses configured normal label path (e.g. Hey.com) only
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_importer_execute.py::test_execute_trial_processes_only_selected_indices -v`
Expected: FAIL because pipeline processes full set

- [ ] **Step 3: Write minimal implementation**

Add selected-index gate in processing loop so only trial-selected rows are processed.
Keep all existing status logic unchanged for processed rows.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_importer_execute.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hey_to_gmail/importer.py tests/test_importer_execute.py
git commit -m "feat: limit trial runs to selected message indices"
```

### Task 5: Add Trial Reporting Fields

**Files:**
- Modify: `src/hey_to_gmail/reporting.py`
- Modify: `tests/test_importer_dry_run.py`
- Modify: `tests/test_importer_execute.py`

- [ ] **Step 1: Write the failing test**

```python
def test_trial_report_contains_trial_columns(trial_run_result):
    row = trial_run_result.rows[0]
    assert "trial_selected" in row
    assert "trial_profile" in row
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_importer_dry_run.py::test_trial_report_contains_trial_columns -v`
Expected: FAIL because columns are missing

- [ ] **Step 3: Write minimal implementation**

Add report fields:
- trial mode rows: `trial_selected=true`, `trial_profile=curated`
- non-trial rows: `trial_selected=false`, `trial_profile=""`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_importer_dry_run.py tests/test_importer_execute.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hey_to_gmail/reporting.py tests/test_importer_dry_run.py tests/test_importer_execute.py
git commit -m "feat: add trial-selected metadata to reports"
```

## Final Verification Checklist

- [ ] Run targeted tests:
  - `pytest tests/test_cli.py -v`
  - `pytest tests/test_importer_dry_run.py -v`
  - `pytest tests/test_importer_execute.py -v`
- [ ] Run full suite: `pytest -v`
- [ ] Manual sanity check:
  - `hey-to-gmail import ... --trial-sample-size 5 --print-trial-only`
  - verify preview shows one expected skip-forwarded and importable candidates

## Notes for Execution

- Keep non-trial behavior unchanged.
- Prefer minimal metadata scanning for selection (avoid loading full message bodies where possible).
- Do not log raw body content in preview/report.
