# HEY MBOX to Gmail Importer Design

## Goal

Import a HEY.com MBOX export into Gmail while skipping messages that were originally forwarded from the user's Gmail account, and label imported messages with `Hey.com` for future cleanup.

## Scope

- Input: one or more local MBOX files
- Output: imported Gmail messages with label `Hey.com`
- Skip condition: strict forwarded-from-Gmail detection using headers observed in the export
- Safety: dry-run support, resumable execution, retry handling, and audit reporting

## Recommended Approach

Use a Python CLI application that streams MBOX messages and imports to Gmail API (`users.messages.import`) with checkpointing in SQLite.

Why this approach:

- Handles large exports without loading everything into memory
- Offers deterministic, resumable behavior for long-running imports
- Uses reliable email parsing support in Python standard library
- Keeps logic and operations in one maintainable tool

## Architecture

### Modules

- `cli.py`: argument parsing and command orchestration
- `mbox_reader.py`: streaming iteration over MBOX messages and extraction of raw RFC822 bytes
- `forwarded_filter.py`: strict Gmail-forwarded detection rules
- `duplicate_detector.py`: local duplicate checks keyed by `Message-ID` and message hash fallback
- `gmail_client.py`: OAuth auth, import calls, label application, retry logic
- `label_manager.py`: ensure `Hey.com` label exists and cache its ID
- `checkpoint_store.py`: SQLite persistence for progress and outcomes
- `reporting.py`: CSV + summary output

### Storage (SQLite)

Authoritative message status enum:

- `pending`
- `imported`
- `imported_unlabeled`
- `skipped_forwarded`
- `skipped_duplicate`
- `failed`

- `runs`: run metadata (start/end timestamps, mode, total files, processing order)
- `files`: one row per input file with normalized path, size, mtime, and ordinal position
- `messages`: per-message status, identifiers, reason, retry counts (`import_retries`, `label_retries`), `hash_version`, and linked `run_id` + `file_id`
- `checkpoints`: explicit resume cursor per file `{file_id, file_size, file_mtime, message_index, message_fingerprint, updated_at}`

## Data Flow

1. Start run in `--dry-run` or `--execute` mode.
2. Ensure OAuth token is available and Gmail label `Hey.com` exists (for execute mode).
3. Stream each message from MBOX.
4. Extract normalized metadata: `Message-ID`, `Date`, `From`, `To`, `Subject`.
5. Evaluate strict forwarded-from-Gmail rule.
6. If forwarded, record `skipped_forwarded` and continue.
7. If not forwarded, run duplicate detection:
   - local table lookup by `Message-ID` when present
   - fallback dedupe key using stable hash for missing `Message-ID`
   - optional remote lookup with Gmail `rfc822msgid:` query when enabled
8. If duplicate, record `skipped_duplicate` and continue.
9. If candidate and mode is `--execute`:
   - call Gmail `users.messages.import` with raw RFC822 content and `labelIds=[hey_label_id]` (primary and required labeling path)
   - if import succeeds but label is missing, mark `imported_unlabeled` and queue `users.messages.modify` label retries
   - if import fails after retries, mark `failed`
10. Record final status for each message in SQLite and CSV.
11. Commit checkpoint every 100 messages and at shutdown.
12. On resume, restart deterministic scan from file start and skip until cursor match (`message_index` + `message_fingerprint`), then continue.

## Forwarded-from-Gmail Detection

### Mode: `strict` (default)

Using configured addresses (`--gmail-address`, `--hey-address`), classify message as forwarded-from-Gmail only when all conditions are true:

1. `X-Forwarded-For` contains both configured Gmail and configured HEY address values
2. `X-Forwarded-To` equals configured HEY address
3. `Delivered-To` equals configured Gmail address

Example from analyzed export:

- `X-Forwarded-For: user@gmail.com user@hey.com`
- `X-Forwarded-To: user@hey.com`
- `Delivered-To: user@gmail.com`

### Mode: `strict_plus` (opt-in)

For users wanting higher recall, classify as forwarded when either `strict` matches or at least two of these predicates match:

1. `X-Forwarded-For` includes configured Gmail and HEY addresses
2. `X-Forwarded-To` equals configured HEY address
3. `Delivered-To` equals configured Gmail address
4. `To` equals configured Gmail address and one `Received` hop indicates Google forwarding path

### Rationale:

- Triple-match (`strict`) reduces false positives.
- `strict_plus` exists to reduce false negatives when one forwarding header is missing or rewritten.
- A pre-execute detection audit report shows counts per rule before any write operation.

## Duplicate Handling

- Primary key: RFC `Message-ID` value (normalized)
- Fallback key: versioned stable hash when `Message-ID` is missing
- Default behavior: skip duplicates

### Fallback hash canonicalization (`hash_version = v1`)

- Header set and order: `Date`, `From`, `To`, `Cc`, `Subject`, `In-Reply-To`, `References`
- Header normalization: unfold continuation lines, trim leading/trailing whitespace, collapse internal whitespace to single space, lowercase header names
- Body selection: prefer `text/plain`; if absent, use `text/html`; if multipart, pick first matching leaf part; if none, use raw body bytes
- Charset handling: decode declared charset when valid, otherwise utf-8 with replacement
- Newline normalization: convert CRLF/CR to LF
- Hash input format: `v1\n<header-name>:<value>...\n\n<body>`
- Hash algorithm: SHA-256 hex digest

## Error Handling and Reliability

- Retries: exponential backoff for transient Gmail API failures (429 and 5xx)
- Failure policy: after retry cap, mark `failed` and continue run
- Resume: validate checkpoint file metadata (`size`, `mtime`) before continuing
- Resume mechanism: always re-scan from start and fast-skip using checkpoint cursor (`message_index`, `message_fingerprint`) plus `messages` table idempotency
- Resume fallback: when metadata mismatch occurs, re-scan from start and rely on persisted outcomes to avoid duplicate actions
- Idempotency: re-runs skip already imported or skipped records from prior state
- Cross-environment idempotency: optional Gmail lookup mode prevents re-import if local state DB is missing

Status transitions:

- `pending -> skipped_forwarded`
- `pending -> skipped_duplicate`
- `pending -> imported`
- `pending -> imported_unlabeled`
- `pending -> failed`
- `imported_unlabeled -> imported` (label retry success)
- `imported_unlabeled -> failed` (label retry exhaustion)

## CLI UX

### Core command

`python -m hey_to_gmail import --mbox <path1> --mbox <path2> --label "Hey.com"`

### Key flags

- `--dry-run` (default): no Gmail writes; produces projected outcomes report
- `--execute`: performs actual import and labeling
- `--mbox <path>`: repeatable input flag; files are processed in provided order
- `--state-db <path>`: override SQLite path
- `--report-csv <path>`: output detailed report
- `--gmail-address <addr>` and `--hey-address <addr>`: detection rule inputs
- `--checkpoint-every <n>`: default 100
- `--forwarded-detection-mode <strict|strict_plus>`: default `strict`
- `--remote-dedupe`: enable Gmail-side duplicate lookup by `Message-ID`
- `--verbose`: include additional diagnostics in logs and CSV (never raw body content)

## Security and Privacy

- OAuth scope: `https://www.googleapis.com/auth/gmail.modify`
- OAuth token cache path: `~/.config/hey-to-gmail/token.json`
- Token file permission target: `0600`
- Refresh behavior: auto-refresh on expiry; if refresh fails, stop and print re-auth steps
- No message body logging to console
- Reports contain metadata and status only; `--verbose` adds diagnostics but still excludes raw body content

## Testing Strategy

- Unit tests for forwarded detection using real sampled header fixtures
- Unit tests for both detection modes (`strict`, `strict_plus`) with expected precision/recall trade-off fixtures
- Unit tests for duplicate logic with and without `Message-ID`
- Unit tests for checkpoint resume behavior
- Integration tests for Gmail client with mocked API responses (retry/label/import paths)
- End-to-end dry-run test on a representative MBOX slice
- Integration test for `imported_unlabeled` recovery path

## Success Criteria

- Tool can process a 632MB export in streaming mode with peak RSS under 250MB in test run
- Memory trend remains steady (no unbounded growth) after initial warm-up period
- Forwarded-from-Gmail messages are skipped under strict rule
- Imported messages are labeled `Hey.com`
- Interrupted run resumes without duplicating imported mail
- Dry-run report aligns with execute-mode outcomes (minus API/runtime failures)

## Out of Scope (Initial Version)

- UI/web dashboard
- Complex multi-label taxonomy
- Cross-account merge heuristics beyond strict duplicate and forwarded rules
