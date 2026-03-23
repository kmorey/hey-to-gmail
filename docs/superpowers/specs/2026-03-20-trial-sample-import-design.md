# Trial Sample Import Design

## Goal

Add a trial mode that imports a small curated subset (default 5) from the HEY MBOX so the user can verify rendering fidelity in Gmail (date/headers/attachments) before full import.

## Confirmed User Choices

- Trial selection style: automatic curated sample
- Trial composition: include one known skipped-forwarded message + four importable candidates
- Trial label behavior: same as normal run (use `Hey.com`, no special trial label)

## Scope

- Add deterministic trial sampling to current CLI/importer flow
- Keep all existing import logic unchanged for non-trial runs
- Add preview output for selected trial messages before processing
- Add reporting fields to distinguish trial-selected rows

Out of scope:

- UI changes
- Custom multi-rule sample templates beyond curated trial profile

## CLI Design

### New Flags

- `--trial-sample-size <n>`
  - Enables trial mode and limits processing to selected sample
  - Default user flow target is `5`
  - Validation:
    - `n <= 0`: error
    - `n = 1`: select one precheck-importable candidate
    - `n >= 2`: require one forwarded candidate + `n-1` importable candidates
- `--trial-profile curated`
  - Curated deterministic selector (default when sample-size is set)
- `--print-trial-only`
  - Prints the selected sample preview and exits without import processing
- `--allow-short-trial`
  - Allows running with fewer than requested selected messages

### Existing Safety Preserved

- Default remains dry-run unless `--execute` is provided
- Trial mode uses exact same import path and label behavior as full run

## Selection Behavior (Curated)

For any `n`, curated composition is:

1. Exactly 1 message expected to be `skipped_forwarded`
2. Remaining `n-1` messages are precheck-importable candidates

For `n = 5`, best-effort diversity target for the 4 importable slots is:

- attachment-present candidate
- Gmail-origin style header candidate
- plain-text-dominant candidate
- general importable fallback candidate

If category coverage is incomplete, fill with next best importable candidates.

Selection must be deterministic:

- Stable by message index/order
- Same inputs produce same selected indices across reruns

### Precheck Predicates

`forwarded candidate`:

- Must satisfy forwarded filter in configured mode.

`precheck-importable candidate`:

- Must NOT satisfy forwarded filter.
- Must have parseable raw bytes.
- Must NOT already be marked terminal duplicate/imported in local state (if state exists).
- Remote dedupe is not required at selection-time; final pipeline may still mark selected rows as duplicate.

Preview action labels are provisional and can differ from final status if later checks (e.g., remote dedupe/API failures) change outcome.

### Deterministic Selection Algorithm

1. Single pass over messages in ascending index.
2. Build candidate lists in scan order:
   - forwarded list
   - importable lists by category: attachment, gmail-origin, plain-text, fallback
3. Select forwarded slot from first forwarded index.
4. Fill importable slots in this fixed priority order:
   - attachment
   - gmail-origin
   - plain-text
   - fallback
5. De-duplicate by index while filling.
6. If still short, continue filling from remaining fallback candidates by ascending index.
7. Final selected order is ascending index.

### Category Heuristics

- `attachment-present`: multipart message with any non-inline part that has `Content-Disposition: attachment`.
- `gmail-origin`: any of:
  - `Message-ID` domain ends with `mail.gmail.com`
  - header `X-Gm-Message-State` present
  - any `Received` hop contains `google.com` and `gmail`
- `plain-text-dominant`: has `text/plain` body; if multipart both text/plain and text/html may exist, still qualifies.
- `fallback`: any precheck-importable candidate not already selected.

## Data Flow Changes

1. Parse flags and enable trial context if `--trial-sample-size` is set.
2. Perform a lightweight scan for candidate selection metadata (index, date/from/subject, forwarded signal, attachment hint).
3. Build deterministic curated selected index set.
4. Print trial selection preview table:
   - index, date, from, subject, expected action (`import`/`skip_forwarded`), attachment hint
5. If `--print-trial-only`, exit successfully.
6. Otherwise run normal importer pipeline but gate processing to selected indices only.
7. Write report rows as normal plus trial fields.

## Error Handling

- If fewer than requested sample size can be selected:
  - fail with clear shortfall error by default
  - continue only when `--allow-short-trial` is set
- If no forwarded candidate exists, report exact reason in shortfall summary
- Preserve existing failure behavior for import retries and status transitions

## Reporting Changes

Add report fields:

- `trial_selected` (boolean)
- `trial_profile` (string, e.g. `curated`)

Keep existing columns and status semantics unchanged.

Report scope in trial mode:

- Output includes only processed rows (selected set), not all scanned rows.
- For trial runs: all rows have `trial_selected=true`, `trial_profile=curated`.
- For non-trial runs: `trial_selected=false`, `trial_profile=""`.

## Implementation Plan (Code Boundaries)

- `src/hey_to_gmail/config.py`
  - add new trial flags and validation rules
- `src/hey_to_gmail/cli.py`
  - parse and pass trial config, print trial preview, support `--print-trial-only`
- `src/hey_to_gmail/importer.py`
  - add trial selector and selected-index gating in pipeline
- `src/hey_to_gmail/reporting.py`
  - extend report schema with trial columns

## Test Strategy

- `tests/test_cli.py`
  - parse/validation for new flags
  - preview-only exit behavior
- `tests/test_importer_dry_run.py`
  - deterministic selection across reruns
  - composition target includes one forwarded + importable set
  - shortfall error and `--allow-short-trial`
- `tests/test_importer_execute.py`
  - execute mode processes selected sample only
  - report includes trial fields

## Success Criteria

- User can run a quick sample import of 5 curated messages
- Sample includes one expected skip-forwarded case and importable candidates
- Preview clearly shows exactly what will be processed
- Execute mode uses unchanged core import logic, only limited by selected indices
- Trial reports are clearly identifiable via trial columns
