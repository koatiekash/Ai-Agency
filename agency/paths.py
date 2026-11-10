"""Where the agency keeps its files.

Locally everything lives in the project folder. On a cloud host, set AGENCY_HOME to the
mounted persistent disk (e.g. /var/agency) so the Command Center database, uploaded data
files and generated output survive restarts and redeploys.
"""
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOME = os.path.abspath(os.environ.get("AGENCY_HOME") or ROOT)
DATA_DIR = os.path.join(HOME, "data")
OUT_DIR = os.path.join(HOME, "output")
for _d in (DATA_DIR, OUT_DIR):
    os.makedirs(_d, exist_ok=True)
