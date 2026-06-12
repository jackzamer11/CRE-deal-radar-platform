"""
Comprehensive class-fit scoring tests — verify all 9 class combinations + normalization edge cases.

All class combinations must produce expected scores; no unexpected fallthrough to neutral 50.
Two-class gaps must score 30 (not excluded, not neutral). Adjacent class gaps must score 70/55.
"""
import pytest
from app.services.match_scoring import (
    _normalize_class,
    _class_rank,
    class_score,
    compute_match,
)


class TestNormalizationAndRanking:
    """Verify _normalize_class and _class_rank work correctly."""

    def test_normalize_class_all_formats(self):
        """Accept all input formats."""
        # Platform format
        assert _normalize_class("Class A") == "Class A"
        assert _normalize_class("Class B") == "Class B"
        assert _normalize_class("Class C") == "Class C"
        # Single letter (CoStar)
        assert _normalize_class("A") == "Class A"
        assert _normalize_class("B") == "Class B"
        assert _normalize_class("C") == "Class C"
        # Case variants
        assert _normalize_class("class a") == "Class A"
        assert _normalize_class("CLASS B") == "Class B"
        assert _normalize_class("cLaSs C") == "Class C"

    def test_class_rank_all_letters(self):
        """Rank for all valid letters."""
        assert _class_rank("A") == 3
        assert _class_rank("B") == 2
        assert _class_rank("C") == 1
        assert _class_rank("Class A") == 3
        assert _class_rank("Class B") == 2
        assert _class_rank("Class C") == 1

    def test_class_rank_invalid_returns_none(self):
        """Invalid inputs return None, which triggers neutral 50."""
        assert _class_rank(None) is None
        assert _class_rank("") is None
        assert _class_rank("D") is None
        assert _class_rank("Trophy") is None


class TestAllNineClassCombinations:
    """Test all 3×3 = 9 class-fit combinations."""

    # Scenario 1: Same class (3 combinations)
    def test_class_a_vs_a(self):
        """A tenant vs A property = 100 (perfect fit)."""
        assert class_score("Class A", "Class A") == 100.0

    def test_class_b_vs_b(self):
        """B tenant vs B property = 100 (perfect fit)."""
        assert class_score("Class B", "Class B") == 100.0

    def test_class_c_vs_c(self):
        """C tenant vs C property = 100 (perfect fit)."""
        assert class_score("Class C", "Class C") == 100.0

    # Scenario 2: One class up (tenant upgrading) (3 combinations)
    def test_class_c_vs_b(self):
        """C tenant vs B property = 70 (tenant upgrading one class)."""
        assert class_score("Class C", "Class B") == 70.0

    def test_class_b_vs_a(self):
        """B tenant vs A property = 70 (tenant upgrading one class)."""
        assert class_score("Class B", "Class A") == 70.0

    def test_class_c_vs_a_not_adjacent(self):
        """C tenant vs A property = 30 (two-class gap, visible but low)."""
        # Note: C→A is 2 ranks, so this is the two-class gap case
        # (C is rank 1, A is rank 3, diff = 3-1 = 2)
        assert class_score("Class C", "Class A") == 30.0

    # Scenario 3: One class down (tenant downgrading) (3 combinations)
    def test_class_b_vs_c(self):
        """B tenant vs C property = 55 (tenant downgrading one class)."""
        assert class_score("Class B", "Class C") == 55.0

    def test_class_a_vs_b(self):
        """A tenant vs B property = 55 (tenant downgrading one class)."""
        assert class_score("Class A", "Class B") == 55.0

    def test_class_a_vs_c(self):
        """A tenant vs C property = 30 (two-class gap, visible but low)."""
        # A is rank 3, C is rank 1, diff = 1-3 = -2
        assert class_score("Class A", "Class C") == 30.0


class TestTwoClassGapAlways30:
    """Verify two-class gaps always score 30 (never excluded, never neutral)."""

    def test_c_to_a_forward(self):
        """C → A: diff = 3 - 1 = 2"""
        assert class_score("Class C", "Class A") == 30.0

    def test_a_to_c_backward(self):
        """A → C: diff = 1 - 3 = -2"""
        assert class_score("Class A", "Class C") == 30.0

    def test_c_to_a_in_composite(self):
        """Verify two-class gap (30) appears in composite."""
        match = compute_match("Tysons", "Tysons", "Class C", "Class A", 5000, 5000)
        assert match is not None
        assert match["class_score"] == 30.0
        # null lease → fallback 25: 0.40·25 + 0.30·100 + 0.15·30 + 0.15·100 = 10+30+4.5+15 = 59.5
        assert match["score"] == pytest.approx(59.5)


class TestNullAndInvalidFallsToNeutral50:
    """Verify null/invalid class triggers neutral 50 fallback."""

    def test_null_tenant_class(self):
        """Null tenant class → neutral 50."""
        assert class_score(None, "Class A") == 50.0

    def test_null_property_class(self):
        """Null property class → neutral 50."""
        assert class_score("Class A", None) == 50.0

    def test_both_null(self):
        """Both null → neutral 50."""
        assert class_score(None, None) == 50.0

    def test_invalid_tenant_class(self):
        """Invalid tenant class → neutral 50."""
        assert class_score("Trophy", "Class A") == 50.0

    def test_invalid_property_class(self):
        """Invalid property class → neutral 50."""
        assert class_score("Class A", "Trophy") == 50.0

    def test_null_in_composite(self):
        """Verify neutral 50 appears in composite when class is null."""
        match = compute_match("Tysons", "Tysons", None, "Class A", 5000, 5000)
        assert match is not None
        assert match["class_score"] == 50.0
        # null lease → fallback 25: 0.40·25 + 0.30·100 + 0.15·50 + 0.15·100 = 10+30+7.5+15 = 62.5
        assert match["score"] == pytest.approx(62.5)


class TestClassScoringNeverReturnsNone:
    """After the two-class gap change, class_score never returns None."""

    def test_all_valid_combinations_non_null(self):
        """All valid class combinations return a score, never None."""
        valid_classes = ["Class A", "Class B", "Class C", "A", "B", "C"]
        for tenant in valid_classes:
            for prop in valid_classes:
                score = class_score(tenant, prop)
                assert score is not None, f"class_score({tenant!r}, {prop!r}) returned None"
                assert 30.0 <= score <= 100.0, f"Score {score} out of range [30, 100]"


class TestEdgeCasesStayVisible:
    """Verify edge cases don't fall back to None or unexpected values."""

    def test_single_letter_mixed_with_platform(self):
        """Single-letter input (CoStar) vs platform format."""
        assert class_score("B", "Class B") == 100.0
        assert class_score("Class B", "B") == 100.0
        assert class_score("A", "C") == 30.0
        assert class_score("C", "A") == 30.0

    def test_whitespace_doesnt_break_ranking(self):
        """Whitespace in input is handled gracefully."""
        assert class_score("  Class A  ", "Class A") == 100.0
        assert class_score("Class A", "  Class B  ") == 55.0

    def test_composite_with_various_sf_gaps(self):
        """Class scoring stable across different SF gaps."""
        # Two-class gap should score 30 regardless of SF fit
        match_tight = compute_match("Tysons", "Tysons", "Class C", "Class A", 5000, 5000)
        match_wide = compute_match("Tysons", "Tysons", "Class C", "Class A", 5000, 5800)

        assert match_tight is not None
        assert match_wide is not None
        assert match_tight["class_score"] == 30.0
        assert match_wide["class_score"] == 30.0
        # SF fit differs, but class is locked at 30
        assert match_tight["sf_fit_score"] == 100.0
        assert match_wide["sf_fit_score"] == 60.0
