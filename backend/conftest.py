"""Root conftest — adds backend/ to sys.path so tests import as app.*"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
