"""
CBRE Q1 2026 benchmark contract (Fix 3 of the post-PR-13 batch).

Locks:
  - SUBMARKET_BENCHMARKS carries the measured Figure 10 values (spot checks:
    Fairfax City 8.5 / 26.23, Crystal City 31.0 / 43.69, Herndon 29.0 / 34.49)
  - no benchmark is flagged PROVISIONAL any more (and no startup warning fires)
  - NOVA_OFFICE_BENCHMARKS market-wide values
  - the email-side benchmark context derives from the config single source
    (Fairfax City tenant-side copy cites 8.5% vacancy)
  - unknown submarkets degrade to no benchmark line — never a crash

No live DB, no network, no CoStar.
"""
import pytest

from app.config import NOVA_OFFICE_BENCHMARKS, SUBMARKET_BENCHMARKS


FIGURE_10 = {
    # submarket: (vacancy %, avg direct asking rate $/SF FSG/yr)
    "Arlington (Clarendon)":     (26.5, 42.93),
    "Arlington (Rosslyn)":       (20.6, 46.85),
    "Arlington (Ballston)":      (21.1, 43.19),
    "Arlington (Columbia Pike)": (22.5, 44.79),
    "Alexandria (Old Town)":     (17.6, 36.73),
    "Tysons":                    (27.3, 39.10),
    "Reston":                    (22.9, 37.84),
    "Falls Church":              (10.4, 27.87),
    "McLean":                    (7.4,  39.21),
    "Vienna":                    (5.2,  24.16),
    "Fairfax City":              (8.5,  26.23),
    "Annandale":                 (17.0, 28.53),
    "Crystal City":              (31.0, 43.69),
    "Merrifield":                (16.4, 33.61),
    "Springfield":               (16.8, 36.22),
    "Herndon":                   (29.0, 34.49),
    "Centreville":               (27.2, 30.49),  # proxy = CBRE "Fairfax Center"
}


@pytest.mark.parametrize("submarket,expected", FIGURE_10.items())
def test_measured_benchmark_values(submarket, expected):
    vacancy, rent = expected
    b = SUBMARKET_BENCHMARKS[submarket]
    assert b["vacancy_pct"] == pytest.approx(vacancy)
    assert b["market_rent_psf"] == pytest.approx(rent)


def test_spot_checks_from_spec():
    assert (SUBMARKET_BENCHMARKS["Fairfax City"]["vacancy_pct"],
            SUBMARKET_BENCHMARKS["Fairfax City"]["market_rent_psf"]) == (8.5, 26.23)
    assert (SUBMARKET_BENCHMARKS["Crystal City"]["vacancy_pct"],
            SUBMARKET_BENCHMARKS["Crystal City"]["market_rent_psf"]) == (31.0, 43.69)
    assert (SUBMARKET_BENCHMARKS["Herndon"]["vacancy_pct"],
            SUBMARKET_BENCHMARKS["Herndon"]["market_rent_psf"]) == (29.0, 34.49)


def test_no_provisional_benchmarks_remain():
    for submarket, b in SUBMARKET_BENCHMARKS.items():
        assert "PROVISIONAL" not in b.get("source", "").upper(), (
            f"{submarket} is still flagged PROVISIONAL — all Figure 10 values are measured"
        )
    # The startup-warning tuple is gone entirely.
    import app.config as config_module
    assert not getattr(config_module, "PROVISIONAL_SUBMARKETS", ()), (
        "PROVISIONAL_SUBMARKETS warnings must not fire for any measured submarket"
    )


def test_no_provisional_warning_fires_on_import(capsys):
    import importlib
    import app.config as config_module
    importlib.reload(config_module)
    captured = capsys.readouterr()
    assert "PROVISIONAL" not in captured.out
    assert "PROVISIONAL" not in captured.err


def test_nova_market_wide_values():
    assert NOVA_OFFICE_BENCHMARKS["avg_vacancy_pct"] == 21.8
    assert NOVA_OFFICE_BENCHMARKS["avg_market_rent_psf"] == 37.49
    assert NOVA_OFFICE_BENCHMARKS["avg_class_a_vacancy_pct"] == 23.5
    assert NOVA_OFFICE_BENCHMARKS["avg_class_a_rent_psf"] == 38.67
    assert NOVA_OFFICE_BENCHMARKS["avg_class_b_vacancy_pct"] == 16.9
    assert NOVA_OFFICE_BENCHMARKS["avg_class_b_rent_psf"] == 32.53


def test_email_benchmark_context_uses_config_single_source():
    """Tenant/owner email copy builds its benchmark line via _submarket_context,
    which must reflect the measured config values (no stale hardcoded copy)."""
    from app.services.property_outreach_service import _submarket_context, CBRE_2026_BENCHMARKS

    # Fairfax City tenant-side email cites 8.5% vacancy
    ctx = _submarket_context("Fairfax City")
    assert "8.5" in ctx and "26.23" in ctx

    # Previously-provisional submarkets now carry measured values in email copy
    ctx = _submarket_context("Crystal City")
    assert "31.0" in ctx and "43.69" in ctx
    ctx = _submarket_context("Herndon")
    assert "29.0" in ctx and "34.49" in ctx

    # The derived dict mirrors config exactly
    assert set(CBRE_2026_BENCHMARKS.keys()) == set(SUBMARKET_BENCHMARKS.keys())


def test_unknown_submarket_degrades_to_no_benchmark_line():
    from app.services.property_outreach_service import _submarket_context
    assert _submarket_context("Leesburg") == ""
    assert _submarket_context(None) == ""
