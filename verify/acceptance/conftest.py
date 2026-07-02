"""Shared fixtures for black-box acceptance tests.

All tests use API_BASE_URL from the environment. No app imports.
"""

import os

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8010")
