"""Filesystem paths used by the server-deploy backtest modules."""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "..", "output")
CONFIG_PATH = os.path.join(BASE_DIR, "..", "config.json")
CACHE_DIR = os.path.join(OUTPUT_DIR, "cache")
