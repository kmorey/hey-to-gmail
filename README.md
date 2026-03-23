# hey-to-gmail

Import HEY email exports into Gmail while intelligently detecting and skipping forwarded messages.

> Note: This entire project was vibe coded with OpenCode and Superpowers.

## Overview

This tool imports your HEY email archive (mbox format) into Gmail. It uses two strategies to identify messages that were originally forwarded to HEY and therefore don't need to be imported back:

1. **Detection from headers** - Checks for forwarding indicators in message headers
2. **Detection from body** - Examines forwarded message formatting patterns

This prevents duplicates and keeps your Gmail clean.

## Installation

### Install uv

Linux/macOS:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify uv is installed:

```bash
uv --version
```

### Install from Source

```bash
# Clone the repository
git clone <repository-url>
cd hey-to-gmail

# Install dependencies and dev tools from pyproject.toml
uv sync --all-extras

# Reproduce the exact locked environment from uv.lock
uv sync --frozen --all-extras
```

Run commands through `uv run` to ensure the project environment is used.

### Run the CLI and tests

```bash
uv run hey-to-gmail import --help
uv run pytest
```

## Google Cloud OAuth Setup

Before using this tool, you need to set up OAuth credentials in Google Cloud:

### 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click "Select a project" → "New Project"
3. Enter a project name (e.g., "hey-to-gmail-import")
4. Click "Create"

### 2. Enable the Gmail API

1. In your project, go to "APIs & Services" → "Library"
2. Search for "Gmail API"
3. Click "Gmail API" and then "Enable"

### 3. Configure the OAuth Consent Screen

1. Go to "APIs & Services" → "OAuth consent screen"
2. Select "External" (or "Internal" if using Google Workspace)
3. Fill in the required fields:
   - App name: "hey-to-gmail"
   - User support email: your email
   - Developer contact information: your email
4. Click "Save and Continue"
5. On the "Scopes" page, click "Add or Remove Scopes"
6. Search for "Gmail API" and select `https://www.googleapis.com/auth/gmail.modify`
7. Click "Update" and then "Save and Continue"
8. Review and click "Back to Dashboard"

### 4. Create OAuth Credentials

1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth client ID"
3. Select "Desktop app" as the application type
4. Name it "hey-to-gmail-cli"
5. Click "Create"
6. Click "Download JSON" to save the credentials file
7. **Move the downloaded file** to your project directory as `credentials.json`

## Authentication

The first time you run the tool, it will open a browser for OAuth authentication:

### Token Location

Authentication tokens are stored at:
```
~/.config/hey-to-gmail/token.json
```

### Token Permissions

- **Token file**: `0600` (owner read/write only)
- **Directory**: `0700` (owner read/write/execute only)

These restrictive permissions ensure your credentials are protected.

### Auto-Refresh Behavior

- Tokens automatically refresh when they expire
- If refresh fails, you'll be prompted to re-authenticate
- Delete `~/.config/hey-to-gmail/token.json` to force re-authentication

## Usage

### Basic Dry-Run (Recommended First Step)

Always run a dry-run first to see what would happen:

```bash
uv run hey-to-gmail import \
  --mbox /path/to/export.mbox \
  --gmail-address your@gmail.com \
  --hey-address your@hey.com
```

This will:
- Analyze all messages in the mbox
- Detect which messages are forwarded
- Generate reports without modifying Gmail

### Execute (Actual Import)

After reviewing the dry-run results, run with `--execute`:

```bash
uv run hey-to-gmail import \
  --mbox /path/to/export.mbox \
  --gmail-address your@gmail.com \
  --hey-address your@hey.com \
  --execute
```

**⚠️ Warning**: This will actually import messages into Gmail. Review the dry-run output first!

## Safety

This tool is designed with safety as a primary concern:

1. **Always run `--dry-run` first** - Default behavior is safe; nothing is imported without explicit confirmation
2. **Review the detection audit** - Check how many messages would be skipped as forwarded
3. **Check the CSV report** - Verify the classification of each message before executing
4. **Default is dry-run mode** - You must explicitly add `--execute` to make changes

## Understanding Output

### Detection Audit

After processing, you'll see counts like:

```
Detection Audit:
  Total messages: 1,000
  Skipped (forwarded): 750
  Would import: 250

Forward detection breakdown:
  Strict mode: 600 forwarded
  Strict+ mode: 750 forwarded
  Difference (lenient): 150 forwarded
```

**Interpretation:**
- **Strict mode**: Conservative detection (fewer false positives)
- **Strict+ mode**: More aggressive detection (catches more forwarded messages)
- The difference shows messages caught by lenient detection only

### CSV Report

A detailed report is saved to `hey_to_gmail_report_YYYYMMDD_HHMMSS.csv`:

| Column | Description |
|--------|-------------|
| `message_id` | Unique identifier from the mbox |
| `status` | `imported`, `skipped_forwarded`, `skipped_duplicate`, or `failed` |
| `reason` | Explanation for the status |
| `timestamp` | ISO timestamp of processing |

**Status values:**
- `imported` - Successfully imported to Gmail
- `skipped_forwarded` - Detected as forwarded, not imported
- `skipped_duplicate` - Already exists in Gmail (by message ID)
- `failed` - Error during import (see reason column)

### SQLite Database

Checkpoint data is stored at `~/.config/hey-to-gmail/checkpoints.db`:

**Schema:**
- `messages` - Tracks processing state for each message
- `imports` - Records of successful imports

**Query for debugging:**
```sql
-- Check processing status
SELECT message_id, status, processed_at 
FROM messages 
WHERE mbox_path = '/path/to/your.mbox';

-- Check import history
SELECT m.message_id, i.gmail_message_id, i.imported_at
FROM messages m
JOIN imports i ON m.id = i.message_id
WHERE m.mbox_path = '/path/to/your.mbox';
```

## Privacy

This tool respects your privacy:

- **No raw email bodies logged** - Only message IDs and metadata are stored
- **Reports contain metadata only** - Subject lines and content are never written to disk
- **Token stored locally** - OAuth tokens never leave your machine
- **No external services** - All processing happens on your computer

## Troubleshooting

### Authentication Errors

**Error**: `Token has been expired or revoked`

**Solution**: Delete the token file and re-authenticate:
```bash
rm ~/.config/hey-to-gmail/token.json
uv run hey-to-gmail import --mbox ... --gmail-address ... --hey-address ...
```

### Rate Limiting

**Symptom**: `429 Too Many Requests` or import slowing down

**Solution**: The tool automatically handles rate limiting with exponential backoff. If you hit limits:
1. Wait and resume - the tool will continue from where it left off
2. The checkpoint store prevents duplicate work

### Resume After Interruption

If the import is interrupted:
1. Simply re-run the same command
2. The tool resumes from the last successful import
3. Already-imported messages are skipped automatically

### Permission Denied on Token File

**Error**: `Permission denied` when accessing token.json

**Solution**: Check and fix permissions:
```bash
chmod 700 ~/.config/hey-to-gmail
chmod 600 ~/.config/hey-to-gmail/token.json
```

## Command Reference

```bash
uv run hey-to-gmail import --help
```

Shows all available options including:
- `--mbox PATH` - Path to mbox file (required)
- `--gmail-address ADDRESS` - Your Gmail address (required)
- `--hey-address ADDRESS` - Your HEY address (required)
- `--execute` - Actually perform the import (default: dry-run)
- `--checkpoints-db PATH` - Custom checkpoint database path
- `--csv-report PATH` - Custom CSV report path

## License

See LICENSE file for details.
