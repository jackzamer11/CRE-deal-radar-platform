from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Deal Radar OS"
    database_url: str = "sqlite:///./deal_radar.db"
    debug: bool = True

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

    # Submarket avg market rents ($/SF/yr NNN)
    submarket_market_rent: dict = {
        "Arlington (Clarendon)": 33.0,
        "Arlington (Rosslyn)": 34.0,
        "Arlington (Ballston)": 34.0,
        "Arlington (Columbia Pike)": 22.0,
        "Alexandria (Old Town)": 26.0,
        "Tysons": 27.0,
        "Reston": 28.5,
        "Falls Church": 23.5,
        "McLean": 33.0,
        "Vienna": 31.33,
        "Fairfax City": 28.0,
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
