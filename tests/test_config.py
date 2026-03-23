"""Tests for configuration defaults and validation."""

import pytest

from hey_to_gmail.config import ImportConfig, TrialConfig


class TestTrialConfigValidation:
    """Tests for trial configuration defaults and validation."""

    def test_trial_sample_size_validation_rejects_zero(self):
        """sample_size must be positive when trial mode is configured."""
        with pytest.raises(ValueError, match="sample_size"):
            TrialConfig(enabled=True, sample_size=0)

    def test_trial_sample_size_validation_rejects_negative(self):
        """sample_size must be positive when trial mode is configured."""
        with pytest.raises(ValueError, match="sample_size"):
            TrialConfig(enabled=True, sample_size=-1)

    def test_trial_profile_defaults_to_curated_when_sample_size_set(self):
        """Trial profile defaults to curated for configured sample sizes."""
        trial = TrialConfig(enabled=True, sample_size=5)
        assert trial.profile == "curated"

    def test_trial_profile_rejects_invalid_value(self):
        """Only supported trial profiles should be accepted."""
        with pytest.raises(ValueError, match="allowed values: curated"):
            TrialConfig(enabled=True, sample_size=5, profile="invalid")


class TestTrialConfigTargetCounts:
    """Tests for trial target counts by sample size."""

    def test_target_counts_for_single_message_sample(self):
        """Single-item trial targets one importable and no forwarded."""
        trial = TrialConfig(enabled=True, sample_size=1)
        assert trial.forwarded_target_count == 0
        assert trial.importable_target_count == 1

    def test_target_counts_for_multi_message_sample(self):
        """Multi-item trial targets one forwarded and the rest importable."""
        trial = TrialConfig(enabled=True, sample_size=5)
        assert trial.forwarded_target_count == 1
        assert trial.importable_target_count == 4


class TestImportConfigDefaults:
    """Tests for import configuration defaults."""

    def test_forwarded_detection_defaults_to_strict_plus(self):
        """Default forwarded detection mode should be strict_plus."""
        config = ImportConfig(
            mbox_paths=["sample.mbox"],
            gmail_address="user@gmail.com",
            hey_address="user@hey.com",
        )
        assert config.forwarded_detection_mode == "strict_plus"
