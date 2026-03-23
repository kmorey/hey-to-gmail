#!/usr/bin/env python3
"""Measure RSS memory usage while importing MBOX files.

This script samples process RSS at regular intervals during MBOX import
to verify memory usage stays within budget and growth is bounded.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple


def get_rss_kb() -> int:
    """Get current process RSS in KB from /proc/self/status.
    
    Returns:
        RSS in KB
    """
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    # Format: "VmRSS:    1234 kB"
                    parts = line.split()
                    return int(parts[1])  # KB value
    except (IOError, ValueError):
        pass
    
    # Fallback: try psutil if available
    try:
        import psutil
        process = psutil.Process()
        return process.memory_info().rss // 1024  # Convert bytes to KB
    except ImportError:
        pass
    
    return 0


def get_rss_mb() -> float:
    """Get current process RSS in MB.
    
    Returns:
        RSS in MB
    """
    return get_rss_kb() / 1024.0


def run_with_rss_sampling(
    mbox_path: Path,
    collect_timeseries: bool = False
) -> Tuple[float, List[Tuple[float, float]]]:
    """Run importer on MBOX file while sampling RSS.
    
    RSS is sampled per-message (not at time intervals) to provide
    fine-grained memory tracking during import processing.
    
    Args:
        mbox_path: Path to MBOX file
        collect_timeseries: Whether to collect full timeseries data
        
    Returns:
        Tuple of (peak_rss_mb, timeseries) where timeseries is a list of
        (timestamp, rss_mb) tuples
    """
    samples = []
    peak_rss_mb = 0.0
    start_time = time.time()
    
    # Create temporary directory for test database and CSV
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        csv_path = Path(tmpdir) / "test.csv"
        
        # Import here to avoid loading heavy modules before measurement
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from hey_to_gmail.importer import MboxImporter
        from hey_to_gmail.mbox_reader import MboxReader
        
        # Initialize importer in dry-run mode (no actual Gmail import)
        importer = MboxImporter(
            mbox_path=mbox_path,
            db_path=db_path,
            csv_path=csv_path,
            mode="dry-run",
            gmail_addr="test@gmail.com",
            hey_addr="test@hey.com",
            gmail_client=None,
            label_manager=None,
            verbose=False
        )
        
        # Sample RSS during processing
        def sample_callback():
            nonlocal peak_rss_mb
            rss_mb = get_rss_mb()
            peak_rss_mb = max(peak_rss_mb, rss_mb)
            if collect_timeseries:
                timestamp = time.time() - start_time
                samples.append((timestamp, rss_mb))
        
        # Monkey-patch the message processing to sample RSS
        original_process_single = importer._process_single_message
        
        def patched_process_single(message_index, email_msg, raw_bytes, fingerprint):
            # Sample before processing
            sample_callback()
            result = original_process_single(message_index, email_msg, raw_bytes, fingerprint)
            return result
        
        importer._process_single_message = patched_process_single
        
        # Run the import
        try:
            importer.run()
        finally:
            # Final sample
            sample_callback()
    
    return peak_rss_mb, samples


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Measure RSS memory usage during MBOX import"
    )
    parser.add_argument(
        "mbox_path",
        type=Path,
        help="Path to MBOX file to process"
    )
    parser.add_argument(
        "--timeseries",
        action="store_true",
        help="Output full RSS timeseries data (samples taken per-message, not at time intervals)"
    )
    parser.add_argument(
        "--warmup-samples",
        type=int,
        default=5,
        help="Number of initial samples to skip as warmup (default: 5)"
    )
    
    args = parser.parse_args()
    
    if not args.mbox_path.exists():
        print(f"Error: MBOX file not found: {args.mbox_path}", file=sys.stderr)
        sys.exit(1)
    
    # Run measurement
    peak_rss_mb, samples = run_with_rss_sampling(
        args.mbox_path,
        collect_timeseries=args.timeseries
    )
    
    # Output results
    print(f"peak_rss_mb: {peak_rss_mb:.2f}")
    
    if samples:
        if args.timeseries:
            for timestamp, rss_mb in samples:
                print(f"sample: {timestamp:.3f} {rss_mb:.2f}")
        
        # Calculate post-warmup growth stats
        warmup_samples = args.warmup_samples
        if len(samples) > warmup_samples:
            post_warmup = samples[warmup_samples:]
            if post_warmup:
                initial_rss = post_warmup[0][1]
                max_rss = max(s[1] for s in post_warmup)
                final_rss = post_warmup[-1][1]
                delta = max_rss - initial_rss
                
                # Calculate slope (simple delta/time for linear trend)
                if len(post_warmup) > 1:
                    time_span = post_warmup[-1][0] - post_warmup[0][0]
                    if time_span > 0:
                        slope = (final_rss - initial_rss) / time_span
                        print(f"post_warmup_slope_mb_per_sec: {slope:.4f}")
                
                print(f"post_warmup_growth_mb: {delta:.2f}")
                print(f"post_warmup_samples: {len(post_warmup)}")
    
    sys.exit(0)


if __name__ == "__main__":
    main()
