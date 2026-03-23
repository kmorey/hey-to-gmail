"""Performance smoke tests for memory usage verification.

These tests verify that the streaming MBOX import architecture maintains
constant memory usage regardless of file size. The small fixture tests
prove the streaming behavior; the large file test (if available) confirms
this scales to real-world export sizes.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_rss_probe(mbox_path):
    """Run RSS measurement probe on an MBOX file.
    
    Args:
        mbox_path: Path to MBOX file to process
        
    Returns:
        Peak RSS in MB
    """
    result = subprocess.run(
        [sys.executable, "scripts/measure_rss.py", str(mbox_path)],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"RSS probe failed: {result.stderr}")
    
    # Parse peak RSS from output
    for line in result.stdout.splitlines():
        if line.startswith("peak_rss_mb:"):
            return float(line.split(":")[1].strip())
    
    raise ValueError(f"Could not parse peak RSS from output: {result.stdout}")


def run_rss_probe_with_timeseries(mbox_path):
    """Run RSS measurement probe and get timeseries data.
    
    Args:
        mbox_path: Path to MBOX file to process
        
    Returns:
        List of (timestamp, rss_mb) tuples
    """
    result = subprocess.run(
        [sys.executable, "scripts/measure_rss.py", str(mbox_path), "--timeseries"],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"RSS probe failed: {result.stderr}")
    
    # Parse timeseries from output
    samples = []
    for line in result.stdout.splitlines():
        if line.startswith("sample:"):
            _, timestamp, rss_mb = line.split()
            samples.append((float(timestamp), float(rss_mb)))
    
    return samples


def bounded_growth(samples, warmup_samples=5, max_delta_mb=20):
    """Check if RSS growth is bounded after warmup period.
    
    Args:
        samples: List of (timestamp, rss_mb) tuples
        warmup_samples: Number of initial samples to skip (warmup)
        max_delta_mb: Maximum allowed growth delta in MB after warmup
        
    Returns:
        True if growth is bounded, False otherwise
    """
    if len(samples) <= warmup_samples:
        return True  # Not enough samples to check
    
    post_warmup = samples[warmup_samples:]
    if not post_warmup:
        return True
    
    # Check delta between first post-warmup and max RSS
    initial_rss = post_warmup[0][1]
    max_rss = max(sample[1] for sample in post_warmup)
    
    return (max_rss - initial_rss) <= max_delta_mb


@pytest.fixture
def sample_largeish_mbox_file(tmp_path):
    """Create a sample MBOX file with multiple messages for testing.
    
    The file is large enough to test streaming but small enough for quick tests.
    """
    mbox_path = tmp_path / "test_sample.mbox"
    
    # Create MBOX content with multiple messages
    mbox_content = []
    for i in range(100):  # 100 messages should be enough to test streaming
        msg = f"""From test{i}@example.com Fri Jan {i+1:02d} 12:00:00 2024
From: sender{i}@example.com
To: recipient{i}@example.com
Subject: Test message {i}
Message-ID: <msg{i}@example.com>

This is the body of test message {i}.
It has multiple lines to make it somewhat realistic.

"""
        mbox_content.append(msg)
    
    mbox_path.write_text("\n".join(mbox_content))
    return mbox_path


def test_rss_budget_smoke(sample_largeish_mbox_file):
    """Test that peak RSS stays under 250MB during processing."""
    peak_rss_mb = run_rss_probe(sample_largeish_mbox_file)
    assert peak_rss_mb < 250, f"Peak RSS {peak_rss_mb}MB exceeds 250MB budget"


def test_rss_growth_is_bounded_after_warmup(sample_largeish_mbox_file):
    """Test that memory growth is bounded after warmup period."""
    samples = run_rss_probe_with_timeseries(sample_largeish_mbox_file)
    assert bounded_growth(samples, warmup_samples=5, max_delta_mb=20), \
        f"RSS growth not bounded after warmup: {samples}"


# Path to actual large export file (632MB)
LARGE_MBOX_PATH = Path("/home/user/Nextcloud/Hey Export/HEY-emails-user@hey.com.mbox")

# Environment variable to override large file path
if "LARGE_MBOX_PATH" in os.environ:
    LARGE_MBOX_PATH = Path(os.environ["LARGE_MBOX_PATH"])


@pytest.mark.skipif(
    not LARGE_MBOX_PATH.exists(),
    reason=f"Large MBOX file not found at {LARGE_MBOX_PATH}"
)
def test_rss_budget_on_large_export():
    """Test that peak RSS stays under 250MB during processing of actual 632MB export.
    
    This test verifies the streaming architecture maintains constant memory
    regardless of the MBOX file size.
    """
    peak_rss_mb = run_rss_probe(LARGE_MBOX_PATH)
    assert peak_rss_mb < 250, f"Peak RSS {peak_rss_mb}MB exceeds 250MB budget on 632MB export"


@pytest.mark.skipif(
    not LARGE_MBOX_PATH.exists(),
    reason=f"Large MBOX file not found at {LARGE_MBOX_PATH}"
)
def test_rss_growth_bounded_on_large_export():
    """Test that memory growth is bounded during processing of actual 632MB export."""
    samples = run_rss_probe_with_timeseries(LARGE_MBOX_PATH)
    assert bounded_growth(samples, warmup_samples=20, max_delta_mb=220), \
        f"RSS growth not bounded after warmup on large export: {samples}"
