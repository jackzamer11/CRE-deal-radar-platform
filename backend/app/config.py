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
        # Nearest-comparable estimates (asking $/SF for sale comps, not CBRE Fig. 10 rents)
        "Annandale": 190,       # ≈ Falls Church
        "Crystal City": 280,    # ≈ Arlington (Ballston)
        "Merrifield": 189,      # ≈ Fairfax City
        "Springfield": 189,     # ≈ Fairfax City
        "Centreville": 189,     # ≈ Fairfax City
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

# All values measured — CBRE Northern Virginia Office Figures Q1 2026, Figure 10
# (vacancy %, avg direct asking rate $/SF FSG/yr).
SUBMARKET_BENCHMARKS = {
    "Arlington (Clarendon)":      {"market_rent_psf": 42.93, "vacancy_pct": 26.5,  "source": "CBRE Q1 2026 Fig. 10 (Clarendon/Courthouse)"},
    "Arlington (Rosslyn)":        {"market_rent_psf": 46.85, "vacancy_pct": 20.6,  "source": "CBRE Q1 2026 Fig. 10"},
    "Arlington (Ballston)":       {"market_rent_psf": 43.19, "vacancy_pct": 21.1,  "source": "CBRE Q1 2026 Fig. 10"},
    "Arlington (Columbia Pike)":  {"market_rent_psf": 44.79, "vacancy_pct": 22.5,  "source": "CBRE Q1 2026 Fig. 10"},
    "Alexandria (Old Town)":      {"market_rent_psf": 36.73, "vacancy_pct": 17.6,  "source": "CBRE Q1 2026 Fig. 10"},
    "Tysons":                     {"market_rent_psf": 39.10, "vacancy_pct": 27.3,  "source": "CBRE Q1 2026 Fig. 10 (Tysons Corner)"},
    "Reston":                     {"market_rent_psf": 37.84, "vacancy_pct": 22.9,  "source": "CBRE Q1 2026 Fig. 10"},
    "Falls Church":               {"market_rent_psf": 27.87, "vacancy_pct": 10.4,  "source": "CBRE Q1 2026 Fig. 10"},
    "McLean":                     {"market_rent_psf": 39.21, "vacancy_pct": 7.4,   "source": "CBRE Q1 2026 Fig. 10 (small sample 0.78 MSF)"},
    "Vienna":                     {"market_rent_psf": 24.16, "vacancy_pct": 5.2,   "source": "CBRE Q1 2026 Fig. 10 (small sample 0.49 MSF)"},
    "Fairfax City":               {"market_rent_psf": 26.23, "vacancy_pct": 8.5,   "source": "CBRE Q1 2026 Fig. 10"},
    "Annandale":                  {"market_rent_psf": 28.53, "vacancy_pct": 17.0,  "source": "CBRE Q1 2026 Fig. 10"},
    "Crystal City":               {"market_rent_psf": 43.69, "vacancy_pct": 31.0,  "source": "CBRE Q1 2026 Fig. 10"},
    "Merrifield":                 {"market_rent_psf": 33.61, "vacancy_pct": 16.4,  "source": "CBRE Q1 2026 Fig. 10"},
    "Springfield":                {"market_rent_psf": 36.22, "vacancy_pct": 16.8,  "source": "CBRE Q1 2026 Fig. 10"},
    "Herndon":                    {"market_rent_psf": 34.49, "vacancy_pct": 29.0,  "source": "CBRE Q1 2026 Fig. 10"},
    # Proxy = CBRE "Fairfax Center" submarket, nearest measured equivalent;
    # revisit when CBRE publishes Centreville-specific data.
    "Centreville":                {"market_rent_psf": 30.49, "vacancy_pct": 27.2,  "source": "CBRE Q1 2026 Fig. 10 (Fairfax Center proxy)"},
}


# ---------------------------------------------------------------------------
# Tenant ↔ Property Match Score configuration
# All point values and weights for the composite Match Score live here —
# nothing is hardcoded inline in the matching services.
# ---------------------------------------------------------------------------

# One-directional adjacency pairs. Symmetry is built programmatically below so
# a one-sided entry can never silently fail a reverse lookup.
_SUBMARKET_ADJACENCY_PAIRS = [
    ("Reston", "Herndon"),
    ("Reston", "Tysons"),
    ("Reston", "Vienna"),
    ("Reston", "McLean"),
    ("Tysons", "McLean"),
    ("Tysons", "Vienna"),
    ("Tysons", "Falls Church"),
    ("Tysons", "Herndon"),
    ("McLean", "Vienna"),
    ("Vienna", "Merrifield"),
    ("Vienna", "Fairfax City"),
    ("Falls Church", "Merrifield"),
    ("Merrifield", "Fairfax City"),
    ("Merrifield", "Annandale"),
    ("Annandale", "Springfield"),
    ("Fairfax City", "Centreville"),
    # Arlington submarkets — all pairwise adjacent
    ("Arlington (Clarendon)", "Arlington (Rosslyn)"),
    ("Arlington (Clarendon)", "Arlington (Ballston)"),
    ("Arlington (Clarendon)", "Arlington (Columbia Pike)"),
    ("Arlington (Rosslyn)", "Arlington (Ballston)"),
    ("Arlington (Rosslyn)", "Arlington (Columbia Pike)"),
    ("Arlington (Ballston)", "Arlington (Columbia Pike)"),
    # Each Arlington submarket ↔ Crystal City
    ("Arlington (Clarendon)", "Crystal City"),
    ("Arlington (Rosslyn)", "Crystal City"),
    ("Arlington (Ballston)", "Crystal City"),
    ("Arlington (Columbia Pike)", "Crystal City"),
    ("Crystal City", "Alexandria (Old Town)"),
]


def _build_symmetric_adjacency(pairs: list) -> dict:
    adjacency: dict = {}
    for a, b in pairs:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    return adjacency


# dict[str, set[str]] — symmetric: b in SUBMARKET_ADJACENCY[a] ⇔ a in SUBMARKET_ADJACENCY[b]
SUBMARKET_ADJACENCY = _build_symmetric_adjacency(_SUBMARKET_ADJACENCY_PAIRS)

# Submarkets selectable in the platform dropdown (mirror of frontend/src/constants.ts).
# A submarket string absent from BOTH this list and the adjacency map degrades
# to exact-match-only — never a crash, never a match-everything wildcard.
PLATFORM_SUBMARKETS = (
    "Arlington (Clarendon)",
    "Arlington (Rosslyn)",
    "Arlington (Ballston)",
    "Arlington (Columbia Pike)",
    "Alexandria (Old Town)",
    "Tysons",
    "Reston",
    "Falls Church",
    "McLean",
    "Vienna",
    "Fairfax City",
    "Annandale",
    "Crystal City",
    "Merrifield",
    "Springfield",
    "Centreville",
)

# Composite Match Score weights (must sum to 1.0)
# lease_expiry 0.40 | submarket 0.30 | class_fit 0.15 | sf_fit 0.15
MATCH_SCORE_WEIGHTS = {
    "lease_expiry": 0.40,
    "submarket":    0.30,
    "class":        0.15,
    "sf_fit":       0.15,
}

# Submarket factor points (non-adjacent pairs are excluded entirely)
SUBMARKET_EXACT_POINTS    = 100.0
SUBMARKET_ADJACENT_POINTS = 60.0

# Building-class factor points (two classes apart → pair excluded)
CLASS_SAME_POINTS      = 100.0   # tenant class == property class
CLASS_UPGRADE_POINTS   = 70.0    # tenant moving up one class (e.g. B tenant → A property)
CLASS_DOWNGRADE_POINTS = 55.0    # tenant moving down one class
CLASS_NEUTRAL_POINTS   = 50.0    # class null/missing/unparseable on either side

# SF-fit hard gate + gradient: |sf_needed − sf_avail| must be ≤ MAX_SF_DELTA
# (hard gate applied BEFORE scoring); surviving pairs score on a linear
# gradient from SF_FIT_MAX_POINTS at delta 0 down to SF_FIT_MIN_POINTS at the cutoff.
MAX_SF_DELTA       = 800
SF_FIT_MAX_POINTS  = 100.0
SF_FIT_MIN_POINTS  = 60.0

# ---------------------------------------------------------------------------
# Vacancy-line citation thresholds for email templates
# Owner-side: cite submarket vacancy only when vacancy_pct > this value
# Tenant-side: cite submarket vacancy only when vacancy_pct < this value
# ---------------------------------------------------------------------------
OWNER_VACANCY_CITE_THRESHOLD  = 15.0  # owner-side: high vacancy signals leasing urgency
TENANT_VACANCY_CITE_THRESHOLD = 10.0  # tenant-side: low vacancy signals tight supply
