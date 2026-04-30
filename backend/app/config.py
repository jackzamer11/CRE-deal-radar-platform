from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Deal Radar OS"
    database_url: str = "sqlite:///./deal_radar.db"
    debug: bool = True

    # Set SEED_ON_INIT=true only on first-ever bootstrap with no real data.
    # Defaults to false so platform restarts never re-insert fake seed records.
    seed_on_init: bool = False

    # Signal engine parameters
    nova_avg_hold_years: float = 7.0
    modern_sf_per_head: int = 175
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
    }

    class Config:
        env_file = ".env"


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
}
