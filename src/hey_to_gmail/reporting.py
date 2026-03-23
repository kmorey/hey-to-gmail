"""Reporting utilities for forwarded email detection and CSV output."""

from datetime import datetime
from typing import Dict


REPORT_FIELDNAMES = [
    "message_id",
    "status",
    "reason",
    "timestamp",
    "trial_selected",
    "trial_profile",
]


def build_report_row(
    *,
    message_id: str,
    status: str,
    reason: str | None,
    trial_selected: bool,
    trial_profile: str,
) -> Dict[str, str]:
    """Build a CSV report row with stable metadata fields."""
    return {
        "message_id": message_id or "",
        "status": status,
        "reason": reason or "",
        "timestamp": datetime.now().isoformat(),
        "trial_selected": "true" if trial_selected else "false",
        "trial_profile": trial_profile,
    }


class DetectionAuditCounter:
    """Counter for tracking detection audit statistics."""
    
    def __init__(self):
        self.strict_matches = 0
        self.strict_plus_matches = 0
        self.total_processed = 0
        self.predicates_histogram: Dict[int, int] = {}
    
    def record(self, strict_match: bool, predicates_matched: int) -> None:
        """Record a detection result.
        
        Args:
            strict_match: Whether strict mode matched
            predicates_matched: Number of predicates that matched
        """
        self.total_processed += 1
        
        if strict_match:
            self.strict_matches += 1
        elif predicates_matched >= 2:
            self.strict_plus_matches += 1
        
        # Track predicate histogram
        self.predicates_histogram[predicates_matched] = (
            self.predicates_histogram.get(predicates_matched, 0) + 1
        )
    
    def summary(self) -> Dict[str, int]:
        """Get a summary of detection statistics.
        
        Returns:
            Dictionary with detection statistics
        """
        return {
            "strict_matches": self.strict_matches,
            "strict_plus_matches": self.strict_plus_matches,
            "total_processed": self.total_processed,
        }
