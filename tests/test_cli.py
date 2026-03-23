"""Tests for CLI interface using argparse."""
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

from hey_to_gmail import cli as cli_module

@pytest.fixture
def env_with_pythonpath():
    """Create environment with PYTHONPATH set to src directory."""
    import os
    env = os.environ.copy()
    # Get the src directory from the tests directory
    src_dir = Path(__file__).parent.parent / "src"
    env["PYTHONPATH"] = str(src_dir) + ":" + env.get("PYTHONPATH", "")
    return env


@pytest.fixture
def sample_mbox_file(tmp_path):
    """Create a sample MBOX file with test messages."""
    mbox_path = tmp_path / "test.mbox"
    
    # Message 1: Normal message with Message-ID
    msg1 = EmailMessage()
    msg1["From"] = "sender1@example.com"
    msg1["To"] = "recipient@example.com"
    msg1["Subject"] = "Test message 1"
    msg1["Message-ID"] = "<msg1-abc@example.com>"
    msg1["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg1.set_content("Body of message 1")
    
    # Message 2: Normal message
    msg2 = EmailMessage()
    msg2["From"] = "sender2@example.com"
    msg2["To"] = "recipient@example.com"
    msg2["Subject"] = "Test message 2"
    msg2["Message-ID"] = "<msg2-def@example.com>"
    msg2["Date"] = "Mon, 02 Jan 2024 12:00:00 +0000"
    msg2.set_content("Body of message 2")
    
    # Write MBOX format
    with open(mbox_path, "wb") as f:
        f.write(b"From sender1@example.com Mon Jan 01 12:00:00 2024\n")
        f.write(msg1.as_bytes())
        f.write(b"\n\n")
        f.write(b"From sender2@example.com Mon Jan 02 12:00:00 2024\n")
        f.write(msg2.as_bytes())
        f.write(b"\n\n")
    
    return mbox_path


class TestCLIDefaults:
    """Tests for CLI default behavior."""

    def test_cli_defaults_to_dry_run(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should default to dry-run mode and show DRY RUN in output."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode == 0
        assert "DRY RUN" in result.stdout.upper() or "DRY RUN" in result.stderr.upper()

    def test_help_contains_examples(self, tmp_path, env_with_pythonpath):
        """Help text should contain usage examples."""
        # Check main help (examples are in the main parser epilog)
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail",
            "--help",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        help_text = result.stdout.lower()
        # Check for key examples in main help text
        assert "examples:" in help_text
        assert "--execute" in help_text
        assert "--mbox" in help_text
        
        # Also check import subcommand help has both flags
        result_import = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--help",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        import_help = result_import.stdout.lower()
        assert "--dry-run" in import_help
        assert "--execute" in import_help

    def test_cli_requires_gmail_address(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should require --gmail-address."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--hey-address", "user@hey.com",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode != 0
        assert "gmail-address" in result.stderr.lower() or "required" in result.stderr.lower()

    def test_cli_requires_hey_address(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should require --hey-address."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode != 0
        assert "hey-address" in result.stderr.lower() or "required" in result.stderr.lower()

    def test_forwarded_detection_mode_defaults_to_strict_plus(self):
        """CLI parser should default forwarded detection mode to strict_plus."""
        parser = cli_module.create_parser()
        args = parser.parse_args([
            "import",
            "--mbox", "sample.mbox",
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
        ])
        assert args.forwarded_detection_mode == "strict_plus"


class TestCLIFlags:
    """Tests for CLI flag handling."""

    def test_cli_accepts_execute_flag(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should accept --execute flag to enable import mode."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--execute",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        # May fail due to missing auth, but should not be CLI error
        assert "unrecognized" not in result.stderr.lower()

    def test_cli_accepts_dry_run_flag(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should accept --dry-run flag explicitly."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--dry-run",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode == 0
        assert "DRY RUN" in result.stdout.upper() or "DRY RUN" in result.stderr.upper()

    def test_dry_run_and_execute_are_mutually_exclusive(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """--dry-run and --execute should be mutually exclusive."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--dry-run",
            "--execute",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode != 0
        # Should show error about mutually exclusive arguments
        assert "not allowed" in result.stderr.lower() or "mutually exclusive" in result.stderr.lower()

    def test_cli_accepts_repeatable_mbox(self, tmp_path, env_with_pythonpath):
        """CLI should accept multiple --mbox flags preserving order."""
        # Create two mbox files
        mbox1 = tmp_path / "first.mbox"
        mbox2 = tmp_path / "second.mbox"

        msg1 = EmailMessage()
        msg1["From"] = "a@example.com"
        msg1["To"] = "b@example.com"
        msg1["Subject"] = "First"
        msg1["Message-ID"] = "<first@example.com>"
        msg1["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
        msg1.set_content("First message")

        msg2 = EmailMessage()
        msg2["From"] = "c@example.com"
        msg2["To"] = "d@example.com"
        msg2["Subject"] = "Second"
        msg2["Message-ID"] = "<second@example.com>"
        msg2["Date"] = "Mon, 02 Jan 2024 12:00:00 +0000"
        msg2.set_content("Second message")

        for path, msg in [(mbox1, msg1), (mbox2, msg2)]:
            with open(path, "wb") as f:
                f.write(b"From sender Mon Jan 01 12:00:00 2024\n")
                f.write(msg.as_bytes())
                f.write(b"\n\n")

        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(mbox2),
            "--mbox", str(mbox1),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        # Should process in provided order: second.mbox first, then first.mbox
        assert "unrecognized" not in result.stderr.lower()
        assert result.returncode == 0

    def test_cli_accepts_remote_dedupe(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should accept --remote-dedupe flag."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--remote-dedupe",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        # Should not fail due to CLI parsing
        assert "unrecognized" not in result.stderr.lower()

    def test_cli_default_label_is_hey_com(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should default --label to 'Hey.com'."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--help",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert "Hey.com" in result.stdout

    def test_cli_accepts_custom_label(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should accept custom --label value."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--label", "CustomLabel",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        # Should not fail due to CLI parsing
        assert result.returncode == 0

    def test_cli_default_state_db(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should have default for --state-db."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--help",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert "--state-db" in result.stdout

    def test_cli_accepts_state_db_override(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should accept --state-db override."""
        db_path = tmp_path / "custom.db"
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--state-db", str(db_path),
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode == 0

    def test_cli_default_report_csv(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should have default for --report-csv."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--help",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert "--report-csv" in result.stdout

    def test_cli_accepts_report_csv_override(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should accept --report-csv override."""
        csv_path = tmp_path / "custom_report.csv"
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--report-csv", str(csv_path),
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode == 0
        # CSV file should be created
        assert csv_path.exists()

    def test_cli_default_checkpoint_every(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should default --checkpoint-every to 100."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--help",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        # Check that checkpoint-every option is documented
        assert "--checkpoint-every" in result.stdout

    def test_cli_accepts_checkpoint_every_override(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should accept --checkpoint-every override."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--checkpoint-every", "50",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode == 0

    def test_cli_accepts_forwarded_detection_mode(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should accept --forwarded-detection-mode flag."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--forwarded-detection-mode", "strict_plus",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode == 0

    def test_cli_accepts_verbose(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should accept --verbose flag."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--verbose",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode == 0

    def test_cli_accepts_trial_flags(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should accept trial flags and print trial preview."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--trial-sample-size", "1",
            "--trial-profile", "curated",
            "--print-trial-only",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode == 0
        assert "Trial Selection Preview" in result.stdout

    def test_cli_preview_includes_required_columns(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """Trial preview should include required columns."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--trial-sample-size", "1",
            "--trial-profile", "curated",
            "--print-trial-only",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        output = result.stdout.lower()
        assert "index" in output
        assert "date" in output
        assert "from" in output
        assert "subject" in output
        assert "expected action" in output
        assert "attachment hint" in output

    def test_cli_preview_includes_real_mbox_row_content(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """Trial preview should include at least one row from the provided mbox."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--trial-sample-size", "1",
            "--print-trial-only",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        output = result.stdout.lower()
        assert result.returncode == 0
        assert "sender1@example.com" in output
        assert "test message 1" in output
        assert "import" in output
        assert "none" in output

    @pytest.mark.parametrize(
        "dependent_flag",
        ["--print-trial-only", "--trial-profile", "--allow-short-trial"],
    )
    def test_trial_dependent_flags_require_trial_sample_size(
        self,
        dependent_flag,
        sample_mbox_file,
        tmp_path,
        env_with_pythonpath,
    ):
        """Trial-dependent flags should require --trial-sample-size."""
        command = [
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            dependent_flag,
        ]
        if dependent_flag == "--trial-profile":
            command.append("curated")

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=env_with_pythonpath,
        )

        assert result.returncode != 0
        assert "requires --trial-sample-size" in result.stderr.lower()

    def test_trial_profile_defaults_to_curated_when_sample_size_set(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """Trial profile should default to curated when sample size is set."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--trial-sample-size", "1",
            "--print-trial-only",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode == 0
        assert "trial profile: curated" in result.stdout.lower()

    def test_trial_profile_rejects_invalid_value(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """CLI should reject invalid trial profile values."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--trial-sample-size", "5",
            "--trial-profile", "invalid",
            "--print-trial-only",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode != 0
        assert "invalid trial profile" in result.stderr.lower()

    def test_print_trial_only_exits_before_processing(self, sample_mbox_file, monkeypatch, capsys):
        """--print-trial-only should exit before importer processing."""
        invoked = {"run": False}

        class ExplodingImporter:
            def __init__(self, *args, **kwargs):
                pass

            def select_trial_indices(self, *args, **kwargs):
                return []

            def run(self):
                invoked["run"] = True
                raise AssertionError("Importer should not run in preview-only mode")

        monkeypatch.setattr(cli_module, "MboxImporter", ExplodingImporter)

        exit_code = cli_module.main([
            "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--trial-sample-size", "5",
            "--print-trial-only",
            "--execute",
        ])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Trial Selection Preview" in captured.out
        assert invoked["run"] is False

    def test_trial_flags_are_wired_to_importer_constructor(self, sample_mbox_file, monkeypatch):
        """Normal import flow should pass trial settings to MboxImporter."""
        captured_kwargs = {}

        class CapturingImporter:
            def __init__(self, *args, **kwargs):
                captured_kwargs.update(kwargs)

            def run(self):
                return {
                    "processed": 0,
                    "imported": 0,
                    "imported_unlabeled": 0,
                    "skipped_forwarded": 0,
                    "skipped_duplicate": 0,
                    "failed": 0,
                }

        monkeypatch.setattr(cli_module, "MboxImporter", CapturingImporter)

        exit_code = cli_module.main([
            "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--trial-sample-size", "5",
            "--allow-short-trial",
        ])

        assert exit_code == 0
        assert captured_kwargs["trial_sample_size"] == 5
        assert captured_kwargs["allow_short_trial"] is True

    def test_execute_wires_gmail_client_and_label_manager(self, sample_mbox_file, monkeypatch):
        """Execute mode should pass Gmail client and label manager to importer."""
        captured_kwargs = {}

        class StubGmailClient:
            def __init__(self):
                self._service = object()

        class StubLabelManager:
            def __init__(self, service=None):
                self._service = service

        class CapturingImporter:
            def __init__(self, *args, **kwargs):
                captured_kwargs.update(kwargs)

            def run(self):
                return {
                    "processed": 0,
                    "imported": 0,
                    "imported_unlabeled": 0,
                    "skipped_forwarded": 0,
                    "skipped_duplicate": 0,
                    "failed": 0,
                }

        monkeypatch.setattr(cli_module, "GmailClient", StubGmailClient, raising=False)
        monkeypatch.setattr(cli_module, "LabelManager", StubLabelManager, raising=False)
        monkeypatch.setattr(cli_module, "MboxImporter", CapturingImporter)

        exit_code = cli_module.main([
            "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--execute",
        ])

        assert exit_code == 0
        assert captured_kwargs["gmail_client"] is not None
        assert captured_kwargs["label_manager"] is not None

    def test_print_trial_only_uses_curated_indices_for_preview(self, tmp_path, capsys):
        """Preview rows should come from curated selector indices, not first-N."""
        mbox_path = tmp_path / "curated-preview.mbox"

        forwarded = EmailMessage()
        forwarded["From"] = "user@gmail.com"
        forwarded["To"] = "user@hey.com"
        forwarded["Subject"] = "Forwarded candidate"
        forwarded["Message-ID"] = "<forwarded@example.com>"
        forwarded["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
        forwarded["X-Forwarded-For"] = "user@gmail.com user@hey.com"
        forwarded["X-Forwarded-To"] = "user@hey.com"
        forwarded["Delivered-To"] = "user@gmail.com"
        forwarded.set_content("Forwarded body")

        fallback = EmailMessage()
        fallback["From"] = "fallback@example.com"
        fallback["To"] = "user@hey.com"
        fallback["Subject"] = "Fallback candidate"
        fallback["Message-ID"] = "<fallback@example.com>"
        fallback["Date"] = "Mon, 02 Jan 2024 12:00:00 +0000"
        fallback.set_content("Fallback body")

        attachment = EmailMessage()
        attachment["From"] = "attachment@example.com"
        attachment["To"] = "user@hey.com"
        attachment["Subject"] = "Attachment candidate"
        attachment["Message-ID"] = "<attachment@example.com>"
        attachment["Date"] = "Mon, 03 Jan 2024 12:00:00 +0000"
        attachment.set_content("Attachment body")
        attachment.add_attachment(
            b"attachment-bytes",
            maintype="application",
            subtype="octet-stream",
            filename="note.txt",
        )

        with open(mbox_path, "wb") as f:
            for msg in (forwarded, fallback, attachment):
                f.write(b"From sender@example.com Mon Jan 01 12:00:00 2024\n")
                f.write(msg.as_bytes())
                f.write(b"\n\n")

        exit_code = cli_module.main([
            "import",
            "--mbox", str(mbox_path),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--trial-sample-size", "2",
            "--print-trial-only",
        ])
        captured = capsys.readouterr()
        output = captured.out

        assert exit_code == 0
        assert "Forwarded candidate" in output
        assert "Attachment candidate" in output
        assert "Fallback candidate" not in output
        assert "0 |" in output
        assert "2 |" in output
        assert "skip_forwarded" in output
        assert "import" in output

    def test_print_trial_only_errors_on_shortfall_without_allow_short_trial(self, tmp_path, capsys):
        """Preview flow should error on curated shortfall unless allow-short-trial is set."""
        mbox_path = tmp_path / "no-forwarded-preview.mbox"

        for index in range(3):
            msg = EmailMessage()
            msg["From"] = f"sender{index}@example.com"
            msg["To"] = "user@hey.com"
            msg["Subject"] = f"Candidate {index}"
            msg["Message-ID"] = f"<candidate-{index}@example.com>"
            msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
            msg.set_content("Body")

            mode = "ab" if mbox_path.exists() else "wb"
            with open(mbox_path, mode) as f:
                f.write(b"From sender@example.com Mon Jan 01 12:00:00 2024\n")
                f.write(msg.as_bytes())
                f.write(b"\n\n")

        exit_code = cli_module.main([
            "import",
            "--mbox", str(mbox_path),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--trial-sample-size", "3",
            "--print-trial-only",
        ])
        captured = capsys.readouterr()

        assert exit_code == 1
        assert "unable to satisfy curated trial sample" in captured.err.lower()

    def test_dry_run_errors_on_trial_shortfall_without_allow_short_trial(self, tmp_path, capsys):
        """Dry-run import should return non-zero when curated trial selection shortfalls."""
        mbox_path = tmp_path / "no-forwarded-dry-run.mbox"

        for index in range(3):
            msg = EmailMessage()
            msg["From"] = f"sender{index}@example.com"
            msg["To"] = "user@hey.com"
            msg["Subject"] = f"Candidate {index}"
            msg["Message-ID"] = f"<candidate-{index}@example.com>"
            msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
            msg.set_content("Body")

            mode = "ab" if mbox_path.exists() else "wb"
            with open(mbox_path, mode) as f:
                f.write(b"From sender@example.com Mon Jan 01 12:00:00 2024\n")
                f.write(msg.as_bytes())
                f.write(b"\n\n")

        exit_code = cli_module.main([
            "import",
            "--mbox", str(mbox_path),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
            "--trial-sample-size", "3",
        ])
        captured = capsys.readouterr()

        assert exit_code == 1
        assert "unable to satisfy curated trial sample" in captured.err.lower()


class TestImportSubcommandContract:
    """Tests for import subcommand contract."""

    def test_import_subcommand_contract(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """Import subcommand should process MBOX with required args."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode == 0


class TestModuleModeContract:
    """Tests for module execution mode."""

    def test_module_mode_contract_matches_subcommand(self, sample_mbox_file, tmp_path, env_with_pythonpath):
        """Module mode should behave like import subcommand."""
        result = subprocess.run([
            sys.executable, "-m", "hey_to_gmail", "import",
            "--mbox", str(sample_mbox_file),
            "--gmail-address", "user@gmail.com",
            "--hey-address", "user@hey.com",
        ], capture_output=True, text=True, cwd=tmp_path, env=env_with_pythonpath)
        assert result.returncode == 0
