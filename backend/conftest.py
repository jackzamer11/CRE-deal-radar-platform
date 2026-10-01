"""Root conftest — adds backend/ to sys.path so tests import as app.*"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

# New entries are read automatically in the background (activity_intel_service
# .schedule_mining). Tests must never reach the API, and backend/.env carries a
# real key, so the suite switches it off before anything is imported. Tests of
# the feature itself call mine_new_entries directly with a stand-in extractor.
os.environ["DEAL_RADAR_AUTO_MINE"] = "0"
