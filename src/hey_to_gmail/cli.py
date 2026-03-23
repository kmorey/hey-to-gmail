"""CLI interface for hey-to-gmail importer."""
import argparse
import logging
import sys
from email.message import Message
from email.message import EmailMessage
from pathlib import Path
from typing import cast

from hey_to_gmail.config import ImportConfig, TrialConfig
from hey_to_gmail.forwarded_filter import is_forwarded_from_gmail
from hey_to_gmail.gmail_client import GmailClient
from hey_to_gmail.mbox_reader import MboxReader
from hey_to_gmail.importer import MboxImporter
from hey_to_gmail.label_manager import LabelManager


def setup_logging(verbose: bool) -> None:
    """Configure logging level."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def create_parser() -> argparse.ArgumentParser:
    """Create and configure argument parser."""
    epilog = """Examples:
  hey-to-gmail import --mbox export.mbox --gmail-address user@gmail.com --hey-address user@hey.com
    Perform a dry-run to preview what would be imported

  hey-to-gmail import --mbox export.mbox --gmail-address user@gmail.com --hey-address user@hey.com --execute
    Execute the actual import to Gmail

  hey-to-gmail import --mbox file1.mbox --mbox file2.mbox --gmail-address user@gmail.com --hey-address user@hey.com
    Import multiple MBOX files in order
"""
    parser = argparse.ArgumentParser(
        prog='hey-to-gmail',
        description='Import emails from HEY MBOX export to Gmail with label management',
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Import subcommand
    import_parser = subparsers.add_parser(
        'import',
        help='Import MBOX files to Gmail'
    )
    
    # Required arguments
    import_parser.add_argument(
        '--mbox',
        action='append',
        required=True,
        help='Path to MBOX file (can be specified multiple times)'
    )
    
    import_parser.add_argument(
        '--gmail-address',
        required=True,
        help='Gmail address for forwarded detection'
    )
    
    import_parser.add_argument(
        '--hey-address',
        required=True,
        help='HEY address for forwarded detection'
    )
    
    # Mode arguments (mutually exclusive)
    mode_group = import_parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        '--dry-run',
        action='store_true',
        default=False,
        help='Dry-run mode (preview only, no changes) [default]'
    )
    mode_group.add_argument(
        '--execute',
        action='store_true',
        default=False,
        help='Execute actual import to Gmail'
    )
    
    import_parser.add_argument(
        '--label',
        default='Hey.com',
        help='Label to apply to imported messages (default: Hey.com)'
    )
    
    import_parser.add_argument(
        '--state-db',
        default='hey-to-gmail.db',
        help='Path to SQLite state database (default: hey-to-gmail.db)'
    )
    
    import_parser.add_argument(
        '--report-csv',
        default='hey-to-gmail-report.csv',
        help='Path to CSV report file (default: hey-to-gmail-report.csv)'
    )
    
    import_parser.add_argument(
        '--checkpoint-every',
        type=int,
        default=100,
        help='Checkpoint interval in messages (default: 100)'
    )
    
    import_parser.add_argument(
        '--forwarded-detection-mode',
        choices=['strict', 'strict_plus'],
        default='strict_plus',
        help='Forwarded detection mode (default: strict_plus)'
    )
    
    import_parser.add_argument(
        '--remote-dedupe',
        action='store_true',
        default=False,
        help='Enable Gmail-side duplicate lookup by Message-ID'
    )
    
    import_parser.add_argument(
        '--verbose',
        action='store_true',
        default=False,
        help='Enable verbose logging'
    )

    import_parser.add_argument(
        '--trial-sample-size',
        type=int,
        default=None,
        help='Enable trial mode with deterministic sample size'
    )

    import_parser.add_argument(
        '--trial-profile',
        default=None,
        help='Trial selection profile (default: curated when trial mode is enabled)'
    )

    import_parser.add_argument(
        '--print-trial-only',
        action='store_true',
        default=False,
        help='Print trial selection preview and exit without running the pipeline'
    )

    import_parser.add_argument(
        '--allow-short-trial',
        action='store_true',
        default=False,
        help='Allow shorter trial sample if full selection cannot be satisfied'
    )
    
    return parser


def validate_config(config: ImportConfig) -> bool:
    """Validate import configuration.
    
    Args:
        config: Import configuration to validate
        
    Returns:
        True if valid, False otherwise
    """
    # Validate MBOX files exist
    for mbox_path in config.mbox_paths:
        if not mbox_path.exists():
            print(f"Error: MBOX file not found: {mbox_path}", file=sys.stderr)
            return False
    
    # Validate checkpoint interval is positive
    if config.checkpoint_every <= 0:
        print("Error: --checkpoint-every must be positive", file=sys.stderr)
        return False
    
    return True


def run_import(config: ImportConfig) -> int:
    """Run the import process.
    
    Args:
        config: Import configuration
        
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    setup_logging(config.verbose)
    logger = logging.getLogger(__name__)

    trial_config = config.trial
    if trial_config and trial_config.print_only:
        preview_rows = []
        for mbox_path in config.mbox_paths:
            preview_importer = MboxImporter(
                mbox_path=mbox_path,
                db_path=config.state_db,
                csv_path=config.report_csv,
                mode="dry-run",
                gmail_addr=config.gmail_address,
                hey_addr=config.hey_address,
                label_name=config.label,
                forwarded_mode=config.forwarded_detection_mode,
                enable_remote_dedupe=config.remote_dedupe,
                checkpoint_interval=config.checkpoint_every,
                trial_sample_size=trial_config.sample_size,
                allow_short_trial=trial_config.allow_short_trial,
                verbose=config.verbose,
            )
            try:
                selected_indices = set(
                    preview_importer.select_trial_indices(
                        sample_size=trial_config.sample_size,
                        allow_short_trial=trial_config.allow_short_trial,
                    )
                )
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1

            for index, msg, _ in MboxReader(mbox_path).stream_messages():
                if index not in selected_indices:
                    continue
                expected_action = (
                    "skip_forwarded"
                    if is_forwarded_from_gmail(
                        cast(EmailMessage, msg),
                        config.gmail_address,
                        config.hey_address,
                        mode=config.forwarded_detection_mode,
                    )
                    else "import"
                )
                preview_rows.append(
                    _build_preview_row(index, msg, expected_action=expected_action)
                )

        print("=" * 60)
        print("Trial Selection Preview")
        print("=" * 60)
        print(f"Trial sample size: {trial_config.sample_size}")
        print(f"Trial profile: {trial_config.profile}")
        print("Columns: index | date | from | subject | expected action | attachment hint")
        for row in preview_rows:
            print(
                f"{row['index']} | {row['date']} | {row['from']} | {row['subject']} "
                f"| {row['expected_action']} | {row['attachment_hint']}"
            )
        if not preview_rows:
            print("(no messages found)")
        print()
        print("Preview-only mode: exiting before pipeline processing.")
        return 0
    
    # Determine mode
    mode = "execute" if config.mode == "execute" else "dry-run"
    
    if mode == "dry-run":
        print("=" * 60)
        print("DRY RUN MODE - No changes will be made to Gmail")
        print("=" * 60)
        print()
    else:
        print("=" * 60)
        print("EXECUTE MODE - Will import messages to Gmail")
        print("=" * 60)
        print()
    
    logger.info(f"Starting import with mode: {mode}")
    logger.info(f"MBOX files: {[str(p) for p in config.mbox_paths]}")
    logger.info(f"Label: {config.label}")
    logger.info(f"State DB: {config.state_db}")
    logger.info(f"Report CSV: {config.report_csv}")

    gmail_client = None
    label_manager = None
    if mode == "execute":
        try:
            gmail_client = GmailClient()
            label_manager = LabelManager(gmail_client._service)
        except Exception as e:
            logger.error(f"Failed to initialize Gmail client: {e}")
            print(f"Error: {e}", file=sys.stderr)
            return 1
    
    # Process each MBOX file
    total_results = {
        "processed": 0,
        "imported": 0,
        "imported_unlabeled": 0,
        "skipped_forwarded": 0,
        "skipped_duplicate": 0,
        "failed": 0,
    }
    
    for mbox_path in config.mbox_paths:
        logger.info(f"Processing {mbox_path}...")
        
        try:
            importer = MboxImporter(
                mbox_path=mbox_path,
                db_path=config.state_db,
                csv_path=config.report_csv,
                mode=mode,
                gmail_addr=config.gmail_address,
                hey_addr=config.hey_address,
                gmail_client=gmail_client,
                label_manager=label_manager,
                label_name=config.label,
                forwarded_mode=config.forwarded_detection_mode,
                enable_remote_dedupe=config.remote_dedupe,
                checkpoint_interval=config.checkpoint_every,
                trial_sample_size=(
                    trial_config.sample_size
                    if trial_config and trial_config.enabled
                    else None
                ),
                allow_short_trial=(
                    trial_config.allow_short_trial
                    if trial_config
                    else False
                ),
                verbose=config.verbose
            )
            
            results = importer.run()
            
            # Accumulate results
            for key in total_results:
                if key in results:
                    total_results[key] += results[key]
            
            logger.info(f"Completed {mbox_path}: {results['processed']} processed")
            
        except Exception as e:
            logger.error(f"Failed to process {mbox_path}: {e}")
            print(f"Error: {e}", file=sys.stderr)
            return 1
    
    # Print summary
    print()
    print("=" * 60)
    print("IMPORT SUMMARY")
    print("=" * 60)
    print(f"Total processed:     {total_results['processed']}")
    print(f"Imported:            {total_results['imported']}")
    print(f"Imported (unlabeled): {total_results['imported_unlabeled']}")
    print(f"Skipped (forwarded): {total_results['skipped_forwarded']}")
    print(f"Skipped (duplicate): {total_results['skipped_duplicate']}")
    print(f"Failed:              {total_results['failed']}")
    print("=" * 60)
    
    # In dry-run, report CSV is already written by importer
    # In execute mode, the same applies
    print(f"\nDetailed report written to: {config.report_csv}")
    
    return 0 if total_results['failed'] == 0 else 1


def main(args=None) -> int:
    """Main CLI entry point.
    
    Args:
        args: Command line arguments (None to use sys.argv)
        
    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    parser = create_parser()
    parsed_args = parser.parse_args(args)
    
    if not parsed_args.command:
        parser.print_help()
        return 1
    
    if parsed_args.command == 'import':
        trial_dependency_used = (
            parsed_args.print_trial_only
            or parsed_args.trial_profile is not None
            or parsed_args.allow_short_trial
        )
        if trial_dependency_used and parsed_args.trial_sample_size is None:
            print(
                "Error: --print-trial-only, --trial-profile, and --allow-short-trial "
                "requires --trial-sample-size",
                file=sys.stderr,
            )
            return 1

        # Build configuration from arguments
        # Determine mode: default is dry-run unless --execute is explicitly passed
        mode = 'execute' if parsed_args.execute else 'dry-run'
        
        trial_config = None
        if parsed_args.trial_sample_size is not None:
            trial_profile = parsed_args.trial_profile or "curated"
            try:
                trial_config = TrialConfig(
                    enabled=True,
                    sample_size=parsed_args.trial_sample_size,
                    profile=trial_profile,
                    print_only=parsed_args.print_trial_only,
                    allow_short_trial=parsed_args.allow_short_trial,
                )
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1

        config = ImportConfig(
            mbox_paths=[Path(p) for p in parsed_args.mbox],
            gmail_address=parsed_args.gmail_address,
            hey_address=parsed_args.hey_address,
            label=parsed_args.label,
            mode=mode,
            state_db=Path(parsed_args.state_db),
            report_csv=Path(parsed_args.report_csv),
            checkpoint_every=parsed_args.checkpoint_every,
            forwarded_detection_mode=parsed_args.forwarded_detection_mode,
            remote_dedupe=parsed_args.remote_dedupe,
            verbose=parsed_args.verbose,
            trial=trial_config,
        )
        
        # Validate configuration
        if not validate_config(config):
            return 1
        
        # Run import
        return run_import(config)
    
    return 0


def _build_preview_row(
    index: int,
    message: Message,
    expected_action: str,
) -> dict[str, str]:
    """Build a single trial preview row from a message."""
    return {
        "index": str(index),
        "date": _clean_preview_value(message.get("Date", ""), default="(no date)"),
        "from": _clean_preview_value(message.get("From", ""), default="(no from)"),
        "subject": _clean_preview_value(message.get("Subject", ""), default="(no subject)"),
        "expected_action": expected_action,
        "attachment_hint": _attachment_hint(message),
    }


def _clean_preview_value(value: str, default: str) -> str:
    """Normalize preview values for single-line output."""
    cleaned = " ".join(str(value).split())
    return cleaned if cleaned else default


def _attachment_hint(message: Message) -> str:
    """Return a lightweight attachment hint for trial preview rows."""
    if not message.is_multipart():
        return "none"

    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            return "has-attachment"
    return "none"


if __name__ == '__main__':
    sys.exit(main())
