# HEY MBOX to Gmail Importer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI that imports HEY MBOX mail into Gmail, skips Gmail-forwarded mail, applies `Hey.com` label, and safely resumes interrupted runs.

**Architecture:** Use a streaming importer that reads MBOX messages one-by-one, classifies forwarded messages, deduplicates, and imports through Gmail API. Persist all run state in SQLite (`runs`, `files`, `messages`, `checkpoints`) and output CSV reports for auditability. Keep `--dry-run` as default and require `--execute` for writes.

**Tech Stack:** Python 3.12+, pytest, sqlite3, mailbox/email stdlib, google-api-python-client, google-auth-oauthlib, argparse

---

## Planned File Structure

- Create: `pyproject.toml` - project metadata, dependencies, scripts, pytest config
- Create: `src/hey_to_gmail/__init__.py` - package marker
- Create: `src/hey_to_gmail/__main__.py` - module execution entrypoint (`python -m hey_to_gmail`)
- Create: `src/hey_to_gmail/cli.py` - CLI parser + command wiring
- Create: `src/hey_to_gmail/config.py` - validated runtime config dataclass
- Create: `src/hey_to_gmail/mbox_reader.py` - streaming message iterator + normalized metadata
- Create: `src/hey_to_gmail/forwarded_filter.py` - strict and strict_plus forwarded detection
- Create: `src/hey_to_gmail/duplicate_detector.py` - Message-ID dedupe + hash v1 fallback
- Create: `src/hey_to_gmail/checkpoint_store.py` - SQLite schema, status transitions, checkpoint/file metadata validation
- Create: `src/hey_to_gmail/gmail_client.py` - OAuth token flow, import/modify calls, remote dedupe lookup, retry logic
- Create: `src/hey_to_gmail/label_manager.py` - ensure label exists and cache label ID lookups
- Create: `src/hey_to_gmail/reporting.py` - CSV output + detection audit counters
- Create: `src/hey_to_gmail/importer.py` - end-to-end processing loop and execution orchestration
- Create: `tests/conftest.py` - fixtures only
- Create: `tests/test_package_import.py` - package bootstrap smoke test
- Create: `tests/test_forwarded_filter.py` - forwarded classification behavior tests
- Create: `tests/test_duplicate_detector.py` - dedupe key and hash canonicalization tests
- Create: `tests/test_checkpoint_store.py` - schema + transitions + checkpoint mismatch tests
- Create: `tests/test_gmail_client.py` - API/retry/auth/permissions tests with mocks
- Create: `tests/test_importer_dry_run.py` - dry-run pipeline, audit report, resume checks
- Create: `tests/test_importer_execute.py` - execute-mode labeling recovery and remote dedupe integration
- Create: `tests/test_cli.py` - CLI defaults, flags, and file ordering
- Create: `tests/test_docs.py` - README safety and setup coverage tests
- Create: `tests/test_performance_smoke.py` - RSS budget and bounded-growth smoke tests
- Create: `scripts/measure_rss.py` - process RSS measurement helper
- Create: `tests/fixtures/headers/forwarded_strict.eml` - strict positive fixture
- Create: `tests/fixtures/headers/not_forwarded_hey_native.eml` - strict negative fixture
- Create: `tests/fixtures/headers/forwarded_missing_one_header.eml` - strict_plus fixture
- Create: `README.md` - setup, OAuth, runbook, troubleshooting, safety notes

### Task 1: Bootstrap Package and Basic Test Wiring

**Files:**
- Create: `pyproject.toml`
- Create: `src/hey_to_gmail/__init__.py`
- Create: `src/hey_to_gmail/__main__.py`
- Create: `tests/test_package_import.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the failing test**

```python
def test_package_importable():
    import hey_to_gmail  # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_package_import.py::test_package_importable -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
```

```python
__all__ = []
```

Create `src/hey_to_gmail/__main__.py` with a temporary `main()` stub that returns non-zero and prints "CLI not wired yet"; wire it to `hey_to_gmail.cli.main()` in Task 7.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_package_import.py::test_package_importable -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/hey_to_gmail/__init__.py tests/test_package_import.py tests/conftest.py
git add src/hey_to_gmail/__main__.py
git commit -m "chore: bootstrap package and pytest wiring"
```

### Task 2: Implement Forwarded Detection + Detection Audit Counters

**Files:**
- Create: `src/hey_to_gmail/forwarded_filter.py`
- Create: `src/hey_to_gmail/reporting.py`
- Create: `tests/fixtures/headers/forwarded_strict.eml`
- Create: `tests/fixtures/headers/not_forwarded_hey_native.eml`
- Create: `tests/fixtures/headers/forwarded_missing_one_header.eml`
- Create: `tests/test_forwarded_filter.py`

- [ ] **Step 1: Write the failing test**

```python
def test_strict_requires_all_three_predicates(sample_forwarded_message):
    assert is_forwarded_from_gmail(sample_forwarded_message, gmail_addr="user@gmail.com", hey_addr="user@hey.com", mode="strict")

def test_strict_plus_accepts_two_of_four(sample_missing_header_forwarded_message):
    assert is_forwarded_from_gmail(sample_missing_header_forwarded_message, gmail_addr="user@gmail.com", hey_addr="user@hey.com", mode="strict_plus")

def test_strict_plus_predicate_four_received_hop(sample_google_received_hop_message):
    assert is_forwarded_from_gmail(sample_google_received_hop_message, gmail_addr="user@gmail.com", hey_addr="user@hey.com", mode="strict_plus")

def test_strict_plus_exactly_two_predicates_boundary(sample_two_predicates_message):
    assert is_forwarded_from_gmail(sample_two_predicates_message, gmail_addr="user@gmail.com", hey_addr="user@hey.com", mode="strict_plus")

def test_audit_counter_tracks_rule_hits():
    counter = DetectionAuditCounter()
    counter.record(strict_match=True, predicates_matched=3)
    assert counter.summary()["strict_matches"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_forwarded_filter.py -v`
Expected: FAIL with missing symbols

- [ ] **Step 3: Write minimal implementation**

```python
def is_forwarded_from_gmail(message, gmail_addr: str, hey_addr: str, mode: str = "strict") -> bool:
    ...
```

```python
class DetectionAuditCounter:
    def record(self, strict_match: bool, predicates_matched: int) -> None: ...
    def summary(self) -> dict[str, int]: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_forwarded_filter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hey_to_gmail/forwarded_filter.py src/hey_to_gmail/reporting.py tests/fixtures/headers tests/test_forwarded_filter.py
git commit -m "feat: add forwarded detection modes and audit counters"
```

### Task 3: Implement Dedupe Key Logic + Remote Dedupe Contract

**Files:**
- Create: `src/hey_to_gmail/duplicate_detector.py`
- Create: `tests/test_duplicate_detector.py`

- [ ] **Step 1: Write the failing test**

```python
def test_message_id_key_preferred(sample_email_message):
    key = dedupe_key_for_message(sample_email_message)
    assert key.kind == "message_id"

def test_hash_v1_stable_across_whitespace_variants(msg_variant_a, msg_variant_b):
    assert dedupe_key_for_message(msg_variant_a).value == dedupe_key_for_message(msg_variant_b).value

def test_hash_v1_uses_required_header_order(sample_email_message):
    ...

def test_hash_v1_prefers_text_plain_then_html(sample_multipart_message):
    ...

def test_hash_v1_normalizes_charset_and_newlines(sample_charset_variant_messages):
    ...

def test_remote_dedupe_skips_lookup_without_message_id(remote_dedupe_checker):
    assert remote_dedupe_checker.should_query_remote(message_id=None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_duplicate_detector.py -v`
Expected: FAIL due to missing module/functions

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class DedupeKey:
    kind: Literal["message_id", "content_hash"]
    value: str
    hash_version: str | None
```

Implement hash v1 canonicalization exactly as spec and keep remote dedupe contract abstracted via a local protocol/callable in this task (no `gmail_client.py` edits yet).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_duplicate_detector.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hey_to_gmail/duplicate_detector.py tests/test_duplicate_detector.py
git commit -m "feat: add local dedupe keys and hash v1 canonicalization"
```

### Task 4: Build SQLite Store with File Metadata Validation and Status Machine

**Files:**
- Create: `src/hey_to_gmail/checkpoint_store.py`
- Create: `tests/test_checkpoint_store.py`

- [ ] **Step 1: Write the failing test**

```python
def test_checkpoint_requires_matching_size_and_mtime(tmp_path):
    store = CheckpointStore(tmp_path / "state.db")
    store.initialize()
    store.upsert_checkpoint(file_id=1, file_size=100, file_mtime=1000.0, message_index=3, message_fingerprint="abc")
    assert store.is_checkpoint_compatible(file_id=1, file_size=100, file_mtime=1000.0)
    assert not store.is_checkpoint_compatible(file_id=1, file_size=200, file_mtime=1000.0)

def test_status_transition_imported_unlabeled_recovery(tmp_path):
    ...

def test_messages_schema_has_reason_retries_hash_version(tmp_path):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_checkpoint_store.py -v`
Expected: FAIL due to missing `CheckpointStore`

- [ ] **Step 3: Write minimal implementation**

Implement schema for `runs`, `files`, `messages`, `checkpoints`, status enum enforcement, and transition helpers.
Include explicit `messages` columns: `reason`, `import_retries`, `label_retries`, `hash_version`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_checkpoint_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hey_to_gmail/checkpoint_store.py tests/test_checkpoint_store.py
git commit -m "feat: add sqlite checkpoint store with status transitions"
```

### Task 5: Implement Gmail Client Auth, Import, Labeling, and Remote Dedupe Query

**Files:**
- Create: `src/hey_to_gmail/gmail_client.py`
- Create: `src/hey_to_gmail/label_manager.py`
- Create: `tests/test_gmail_client.py`

- [ ] **Step 1: Write the failing test**

```python
def test_import_uses_label_ids(gmail_service_mock, sample_raw_email):
    client = GmailClient(service=gmail_service_mock)
    client.import_message(raw_bytes=sample_raw_email, label_ids=["LBL_HEY"])
    gmail_service_mock.users.return_value.messages.return_value.import_.assert_called_once()

def test_remote_dedupe_queries_rfc822msgid(gmail_service_mock):
    client = GmailClient(service=gmail_service_mock)
    client.message_exists_by_rfc822msgid("<abc@example.com>")
    gmail_service_mock.users.return_value.messages.return_value.list.assert_called_once()

def test_token_file_permission_is_0600(tmp_path):
    ...

def test_ensure_label_reuses_cached_id(gmail_service_mock):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gmail_client.py -v`
Expected: FAIL due to missing `GmailClient`

- [ ] **Step 3: Write minimal implementation**

Implement:
- OAuth scope `https://www.googleapis.com/auth/gmail.modify`
- token path `~/.config/hey-to-gmail/token.json`
- token permission enforcement `0600`
- retry/backoff for 429/5xx
- `message_exists_by_rfc822msgid()` using Gmail query
- refresh-failure error with actionable re-auth guidance
- `LabelManager.ensure_label("Hey.com")` with in-process ID cache and create-if-missing behavior

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gmail_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hey_to_gmail/gmail_client.py tests/test_gmail_client.py
git add src/hey_to_gmail/label_manager.py
git commit -m "feat: add gmail client and label manager workflows"
```

### Task 6: Build Importer Core (Dry Run + Execute + Resume Fallback)

**Files:**
- Create: `src/hey_to_gmail/mbox_reader.py`
- Create: `src/hey_to_gmail/importer.py`
- Modify: `src/hey_to_gmail/reporting.py`
- Create: `tests/test_importer_dry_run.py`
- Create: `tests/test_importer_execute.py`

- [ ] **Step 1: Write the failing test**

```python
def test_resume_with_metadata_mismatch_forces_rescan(tmp_path, sample_mbox_file):
    ...

def test_shutdown_flushes_checkpoint_below_interval(tmp_path, sample_mbox_file):
    ...

def test_execute_recovers_imported_unlabeled_to_imported(tmp_path, mocked_gmail_client, sample_mbox_file):
    ...

def test_execute_marks_failed_on_label_retry_exhaustion(tmp_path, mocked_gmail_client, sample_mbox_file):
    ...

def test_verbose_output_never_includes_raw_body(tmp_path, sample_mbox_file, caplog):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_importer_dry_run.py tests/test_importer_execute.py -v`
Expected: FAIL due to missing importer logic

- [ ] **Step 3: Write minimal implementation**

Implement importer flow:
- detection audit report generated before execute writes
- forwarded skip decisions
- local dedupe + optional remote dedupe
- primary import with `labelIds`
- recovery path `imported_unlabeled -> imported|failed`
- metadata validation + full-rescan fallback with idempotent skipping

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_importer_dry_run.py tests/test_importer_execute.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hey_to_gmail/mbox_reader.py src/hey_to_gmail/importer.py src/hey_to_gmail/reporting.py tests/test_importer_dry_run.py tests/test_importer_execute.py
git commit -m "feat: add importer pipeline with resume fallback and recovery paths"
```

### Task 7: Add CLI and Config Wiring for Full Spec Flags

**Files:**
- Create: `src/hey_to_gmail/config.py`
- Create: `src/hey_to_gmail/cli.py`
- Modify: `src/hey_to_gmail/__main__.py`
- Create: `tests/test_cli.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing test**

```python
def test_cli_defaults_to_dry_run(cli_runner, sample_mbox_file):
    ...

def test_cli_accepts_repeatable_mbox_and_remote_dedupe(cli_runner, sample_mbox_file):
    ...

def test_import_subcommand_contract(cli_runner, sample_mbox_file):
    result = cli_runner.invoke([
        "import",
        "--mbox", str(sample_mbox_file),
        "--gmail-address", "user@gmail.com",
        "--hey-address", "user@hey.com",
    ])
    assert result.exit_code == 0

def test_module_mode_contract_matches_subcommand(cli_runner, sample_mbox_file):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL due to missing parser/entrypoint

- [ ] **Step 3: Write minimal implementation**

Implement flags:
- repeatable `--mbox`
- `--dry-run` default / `--execute`
- `--label` (default `Hey.com`)
- `--gmail-address` and `--hey-address`
- `--forwarded-detection-mode`
- `--remote-dedupe`
- `--state-db`
- `--report-csv`
- `--checkpoint-every`
- `--verbose`

Add tests for defaults and override values of `--state-db`, `--report-csv`, `--checkpoint-every`, and `--label`.
Add tests that assert repeatable `--mbox` preserves provided processing order.

Add script entrypoint:

```toml
[project.scripts]
hey-to-gmail = "hey_to_gmail.cli:main"
```

Update `src/hey_to_gmail/__main__.py` to delegate to `hey_to_gmail.cli.main()` and add test assertions for module-mode parity.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hey_to_gmail/config.py src/hey_to_gmail/cli.py tests/test_cli.py pyproject.toml
git add src/hey_to_gmail/__main__.py
git commit -m "feat: add cli and config for full importer workflow"
```

### Task 8: Add Documentation and Safety Runbook

**Files:**
- Create: `README.md`
- Create: `tests/test_docs.py`

- [ ] **Step 1: Write the failing test**

```python
def test_readme_covers_safety_and_auth_requirements():
    text = Path("README.md").read_text()
    assert "--dry-run" in text
    assert "--execute" in text
    assert "gmail.modify" in text
    assert "~/.config/hey-to-gmail/token.json" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_docs.py::test_readme_covers_safety_and_auth_requirements -v`
Expected: FAIL because README not present

- [ ] **Step 3: Write minimal implementation**

Document:
- OAuth setup and consent flow
- token permissions (`0600`) and refresh behavior
- dry-run-first migration workflow
- execute workflow
- forwarded detection audit interpretation
- CSV/SQLite output interpretation
- privacy guarantees (no raw body logs)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_docs.py::test_readme_covers_safety_and_auth_requirements -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_docs.py
git commit -m "docs: add oauth setup and safe migration runbook"
```

### Task 9: Add Measurable Memory Verification

**Files:**
- Create: `tests/test_performance_smoke.py`
- Create: `scripts/measure_rss.py`

- [ ] **Step 1: Write the failing test**

```python
def test_rss_budget_smoke(sample_largeish_mbox_file):
    peak_rss_mb = run_rss_probe(sample_largeish_mbox_file)
    assert peak_rss_mb < 250

def test_rss_growth_is_bounded_after_warmup(sample_largeish_mbox_file):
    samples = run_rss_probe_with_timeseries(sample_largeish_mbox_file)
    assert bounded_growth(samples, warmup_samples=5, max_delta_mb=20)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_performance_smoke.py::test_rss_budget_smoke -v`
Expected: FAIL because measurement helper not implemented

- [ ] **Step 3: Write minimal implementation**

Implement `scripts/measure_rss.py` to sample process RSS while importing a representative fixture and emit peak MB.
Also emit RSS timeseries and compute post-warmup growth delta/slope used by the bounded-growth assertion.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_performance_smoke.py::test_rss_budget_smoke -v`
Expected: PASS on local fixture baseline

- [ ] **Step 5: Commit**

```bash
git add tests/test_performance_smoke.py scripts/measure_rss.py
git commit -m "test: add importer memory usage smoke check"
```

## Final Verification Checklist

- [ ] Run full tests: `pytest -v`
- [ ] Run dry-run command on sampled real headers and verify detection audit counts
- [ ] Run execute-mode test suite with mocked Gmail API
- [ ] Run RSS measurement smoke test and record peak value

## Notes for Execution

- Keep implementation YAGNI and avoid non-spec features.
- Do not log raw email body content, even in verbose mode.
- Preserve strict mode as default.
- Commit after each task.
