"""A tiny stand-in for the Supabase license table. The model never touches this directly."""
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LICENSES_FILE = ROOT / "data" / "licenses.json"
CODE_PATTERN = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")


def lookup_license(install_code: str, today: date | None = None) -> dict:
    """Return the license status for one installation code as a plain dict."""
    today = today or date.today()
    code = install_code.strip().upper()

    if not CODE_PATTERN.match(code):
        return {"found": False, "install_code": code,
                "error": "Invalid format. An installation code looks like ABCD-1234 (4 characters, dash, 4 characters)."}

    with LICENSES_FILE.open(encoding="utf-8") as f:
        records = {r["install_code"]: r for r in json.load(f)}

    record = records.get(code)
    if record is None:
        return {"found": False, "install_code": code, "error": "No license is registered for this installation code."}

    expires_on = date.fromisoformat(record["expires_on"])
    if not record["is_active"]:
        status = "turned_off"
    elif expires_on < today:
        status = "expired"
    else:
        status = "active"

    return {
        "found": True,
        "install_code": code,
        "status": status,
        "expires_on": record["expires_on"],
        "days_left": (expires_on - today).days,
        "today": today.isoformat(),
    }