import os
from typing import Optional
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Resolve .env relative to this file so it works regardless of CWD.
# backend/app/config.py → backend/.env
_env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(_env_file, override=False)


class Settings(BaseSettings):
    app_name: str = "Deal Radar OS"
    database_url: str = "sqlite:///./deal_radar.db"
    debug: bool = True

    # Set SEED_ON_INIT=true only on first-ever bootstrap with no real data.
    # Defaults to false so platform restarts never re-insert fake seed records.
    seed_on_init: bool = False

    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None

    # Signal engine parameters
    nova_avg_hold_years: float = 7.0
    cramped_sf_per_head: int = 150

    # Submarket reference data (avg asking $/SF for comps)
    submarket_avg_psf: dict = {
        "Arlington (Clarendon)": 310,
        "Arlington (Rosslyn)": 295,
        "Arlington (Ballston)": 280,
        "Arlington (Columbia Pike)": 195,
        "Alexandria (Old Town)": 260,
        "Tysons": 240,
        "Reston": 265,
        "Falls Church": 190,
        "McLean": 243,
        "Vienna": 218,
        "Fairfax City": 189,
        # Provisional (nearest-comparable) — see PROVISIONAL_SUBMARKETS warning below
        "Annandale": 190,       # ≈ Falls Church
        "Crystal City": 280,    # ≈ Arlington (Ballston)
        "Merrifield": 189,      # ≈ Fairfax City
        "Springfield": 189,     # ≈ Fairfax City
        "Centreville": 189,     # ≈ Fairfax City
        "Herndon": 265,         # ≈ Dulles Corridor (Reston proxy)
    }

    # Submarket avg market rents ($/SF/yr NNN) — updated to CBRE Q1 2026
    submarket_market_rent: dict = {
        "Arlington (Clarendon)":     42.93,
        "Arlington (Rosslyn)":       46.85,
        "Arlington (Ballston)":      43.19,
        "Arlington (Columbia Pike)": 28.22,
        "Alexandria (Old Town)":     36.73,
        "Tysons":                    39.10,
        "Reston":                    37.84,
        "Falls Church":              27.87,
        "McLean":                    39.21,
        "Vienna":                    24.16,
        "Fairfax City":              26.23,
        # Provisional (nearest-comparable)
        "Annandale":    27.87,   # ≈ Falls Church
        "Crystal City": 43.19,   # ≈ Arlington (Ballston)
        "Merrifield":   26.23,   # ≈ Fairfax City
        "Springfield":  26.23,   # ≈ Fairfax City
        "Centreville":  26.23,   # ≈ Fairfax City
        "Herndon":      37.84,   # ≈ Dulles Corridor (Reston proxy)
    }

    # Submarket avg cap rates
    submarket_cap_rate: dict = {
        "Arlington (Clarendon)": 6.0,
        "Arlington (Rosslyn)": 5.9,
        "Arlington (Ballston)": 6.2,
        "Arlington (Columbia Pike)": 7.2,
        "Alexandria (Old Town)": 6.8,
        "Tysons": 6.2,
        "Reston": 6.5,
        "Falls Church": 7.0,
        "McLean": 9.7,
        "Vienna": 9.5,
        "Fairfax City": 10.2,
        # Provisional (nearest-comparable)
        "Annandale":    7.0,    # ≈ Falls Church
        "Crystal City": 6.2,    # ≈ Arlington (Ballston)
        "Merrifield":   10.2,   # ≈ Fairfax City
        "Springfield":  10.2,   # ≈ Fairfax City
        "Centreville":  10.2,   # ≈ Fairfax City
        "Herndon":      6.5,    # ≈ Dulles Corridor (Reston proxy)
    }

    # Submarket avg days on market
    submarket_avg_dom: dict = {
        "Arlington (Clarendon)": 95,
        "Arlington (Rosslyn)": 110,
        "Arlington (Ballston)": 100,
        "Arlington (Columbia Pike)": 145,
        "Alexandria (Old Town)": 120,
        "Tysons": 130,
        "Reston": 115,
        "Falls Church": 150,
        "McLean": 560,
        "Vienna": 290,
        "Fairfax City": 252,
        # Provisional (nearest-comparable)
        "Annandale":    150,    # ≈ Falls Church
        "Crystal City": 100,    # ≈ Arlington (Ballston)
        "Merrifield":   252,    # ≈ Fairfax City
        "Springfield":  252,    # ≈ Fairfax City
        "Centreville":  252,    # ≈ Fairfax City
        "Herndon":      115,    # ≈ Dulles Corridor (Reston proxy)
    }

    class Config:
        env_file = _env_file


settings = Settings()


# ---------------------------------------------------------------------------
# CBRE Q1 2026 Northern Virginia Office Market Benchmarks
# Source: CBRE Research, Q1 2026 (Northern Virginia Office Figures)
# Update quarterly when new CBRE report releases
# ---------------------------------------------------------------------------

# Source: CBRE Research, Q1 2026 (Northern Virginia Office Figures)
# Update quarterly when new CBRE report releases (scheduled task: update-cbre-nova-benchmarks)
NOVA_OFFICE_BENCHMARKS = {
    "avg_market_rent_psf":      37.49,
    "avg_vacancy_pct":          21.8,
    "avg_class_a_rent_psf":     38.67,
    "avg_class_a_vacancy_pct":  23.5,
    "avg_trophy_rent_psf":      62.96,
    "avg_trophy_vacancy_pct":   13.9,
    "avg_class_b_rent_psf":     32.53,
    "avg_class_b_vacancy_pct":  16.9,
    "avg_free_rent_months":     6,        # ESTIMATE — verify when CompStak active
    "avg_ti_psf":               60,       # ESTIMATE — verify when CompStak active
    "avg_lease_term_years":     7,        # NoVA office standard
    "data_source": "CBRE Research, Q1 2026",
    "data_as_of":  "2026-Q1",
}

SUBMARKET_BENCHMARKS = {
    "Arlington (Clarendon)":      {"market_rent_psf": 42.93, "vacancy_pct": 26.5,  "source": "CBRE Q1 2026 (Clarendon/Courthouse)"},
    "Arlington (Rosslyn)":        {"market_rent_psf": 46.85, "vacancy_pct": 20.6,  "source": "CBRE Q1 2026"},
    "Arlington (Ballston)":       {"market_rent_psf": 43.19, "vacancy_pct": 21.1,  "source": "CBRE Q1 2026"},
    "Arlington (Columbia Pike)":  {"market_rent_psf": 28.22, "vacancy_pct": 32.1,  "source": "CBRE Q1 2026 (I-395 Corridor Arlington — verify)"},
    "Alexandria (Old Town)":      {"market_rent_psf": 36.73, "vacancy_pct": 17.6,  "source": "CBRE Q1 2026"},
    "Tysons":                     {"market_rent_psf": 39.10, "vacancy_pct": 27.3,  "source": "CBRE Q1 2026 (Tysons Corner)"},
    "Reston":                     {"market_rent_psf": 37.84, "vacancy_pct": 22.9,  "source": "CBRE Q1 2026"},
    "Falls Church":               {"market_rent_psf": 27.87, "vacancy_pct": 10.4,  "source": "CBRE Q1 2026"},
    "McLean":                     {"market_rent_psf": 39.21, "vacancy_pct": 7.4,   "source": "CBRE Q1 2026 (small sample 0.78 MSF)"},
    "Vienna":                     {"market_rent_psf": 24.16, "vacancy_pct": 5.2,   "source": "CBRE Q1 2026 (small sample 0.49 MSF)"},
    "Fairfax City":               {"market_rent_psf": 26.23, "vacancy_pct": 8.5,   "source": "CBRE Q1 2026"},
    # ── Provisional benchmarks (nearest-comparable submarket) ────────────────
    # Added so newly-mapped CoStar submarkets render with sensible market
    # context instead of falling through to the generic NoVA default. These are
    # NOT measured CBRE figures — replace when submarket-specific data is available.
    "Annandale":                  {"market_rent_psf": 27.87, "vacancy_pct": 10.4,  "source": "PROVISIONAL ≈ Falls Church"},
    "Crystal City":               {"market_rent_psf": 43.19, "vacancy_pct": 21.1,  "source": "PROVISIONAL ≈ Arlington (Ballston)"},
    "Merrifield":                 {"market_rent_psf": 26.23, "vacancy_pct": 8.5,   "source": "PROVISIONAL ≈ Fairfax City"},
    "Springfield":                {"market_rent_psf": 26.23, "vacancy_pct": 8.5,   "source": "PROVISIONAL ≈ Fairfax City"},
    "Centreville":                {"market_rent_psf": 26.23, "vacancy_pct": 8.5,   "source": "PROVISIONAL ≈ Fairfax City"},
    "Herndon":                    {"market_rent_psf": 37.84, "vacancy_pct": 22.9,  "source": "PROVISIONAL ≈ Dulles Corridor (Reston proxy)"},
}

# Submarkets whose benchmark data is provisional (nearest-comparable, not measured).
PROVISIONAL_SUBMARKETS = ("Annandale", "Crystal City", "Merrifield", "Springfield", "Centreville", "Herndon")
for _sm in PROVISIONAL_SUBMARKETS:
    print(
        f"[config] WARNING: benchmark for submarket '{_sm}' is PROVISIONAL "
        f"({SUBMARKET_BENCHMARKS[_sm]['source']}) — replace with measured data when available."
    )
