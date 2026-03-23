"""Configuration management for hey-to-gmail."""
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TrialConfig:
    """Configuration for deterministic trial sample selection."""

    enabled: bool = False
    sample_size: int = 0
    profile: str = "curated"
    print_only: bool = False
    allow_short_trial: bool = False

    def __post_init__(self):
        """Validate trial configuration values."""
        if not self.enabled:
            return

        if self.sample_size <= 0:
            raise ValueError("trial sample_size must be positive")

        allowed_profiles = {"curated"}
        if self.profile not in allowed_profiles:
            allowed_values = ", ".join(sorted(allowed_profiles))
            raise ValueError(
                f"invalid trial profile '{self.profile}'; allowed values: {allowed_values}"
            )

    @property
    def forwarded_target_count(self) -> int:
        """Number of forwarded messages to target for this trial sample."""
        if not self.enabled or self.sample_size <= 1:
            return 0
        return 1

    @property
    def importable_target_count(self) -> int:
        """Number of importable messages to target for this trial sample."""
        if not self.enabled:
            return 0
        if self.sample_size == 1:
            return 1
        return self.sample_size - 1


@dataclass
class ImportConfig:
    """Configuration for import operation."""
    
    # Required
    mbox_paths: List[Path]
    gmail_address: str
    hey_address: str
    
    # Optional with defaults
    label: str = "Hey.com"
    mode: str = "dry-run"  # "dry-run" or "execute"
    state_db: Path = Path("hey-to-gmail.db")
    report_csv: Path = Path("hey-to-gmail-report.csv")
    checkpoint_every: int = 100
    forwarded_detection_mode: str = "strict_plus"  # "strict" or "strict_plus"
    remote_dedupe: bool = False
    verbose: bool = False
    trial: Optional[TrialConfig] = None
    
    def __post_init__(self):
        """Convert string paths to Path objects if needed."""
        if isinstance(self.mbox_paths, str):
            self.mbox_paths = [Path(self.mbox_paths)]
        elif self.mbox_paths and isinstance(self.mbox_paths[0], str):
            self.mbox_paths = [Path(p) for p in self.mbox_paths]
        
        if isinstance(self.state_db, str):
            self.state_db = Path(self.state_db)
        
        if isinstance(self.report_csv, str):
            self.report_csv = Path(self.report_csv)
