"""A fake SolarDesk license database in SQLite, rebuilt from code on every run.

The data is fixed and 'today' is fixed, so the expected answer of every golden question
never changes. A real database (or date('now')) would make yesterday's correct answer wrong.
"""
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB_FILE = ROOT / "data" / "nl2sql" / "licenses.db"
TODAY = "2026-11-06"

SCHEMA = """\
CREATE TABLE customers (
    id           INTEGER PRIMARY KEY,
    company      TEXT NOT NULL,      -- company name, e.g. 'Anadolu Solar A.Ş.'
    city         TEXT NOT NULL,
    contact_name TEXT NOT NULL,      -- the person we talk to
    signed_up_on TEXT NOT NULL       -- ISO date 'YYYY-MM-DD'
);

CREATE TABLE licenses (
    install_code  TEXT PRIMARY KEY,  -- code shown on first launch, e.g. '7F3K-92QD'
    customer_id   INTEGER NOT NULL REFERENCES customers(id),
    computer_label TEXT NOT NULL,    -- which computer, e.g. 'Ofis-1', 'Laptop'
    registered_on TEXT NOT NULL,     -- ISO date the code was added
    expires_on    TEXT NOT NULL,     -- ISO date the license stops working
    is_active     INTEGER NOT NULL,  -- 1 = on, 0 = turned off by the team (e.g. computer changed)
    price_try     INTEGER NOT NULL   -- yearly price in Turkish lira
);
"""

CUSTOMERS = [
    (1, "Güneş Enerji Ltd.", "İzmir", "Ayşe Kaya", "2025-03-10"),
    (2, "Anadolu Solar A.Ş.", "Konya", "Mehmet Demir", "2025-05-22"),
    (3, "Ege Montaj", "İzmir", "Can Yılmaz", "2025-07-01"),
    (4, "Toros GES", "Mersin", "Elif Şahin", "2025-09-15"),
    (5, "Karadeniz Yapı", "Trabzon", "Murat Çelik", "2025-11-03"),
    (6, "Marmara Çelik", "İstanbul", "Zeynep Arslan", "2026-01-12"),
    (7, "Akdeniz Enerji", "Antalya", "Burcu Koç", "2026-02-20"),
    (8, "Kapadokya Solar", "Nevşehir", "Hakan Aydın", "2026-03-05"),
    (9, "İç Anadolu Proje", "Ankara", "Selin Öztürk", "2026-04-18"),
    (10, "Doğu Güneş", "Erzurum", "Emre Kılıç", "2026-06-09"),
    (11, "Trakya Mühendislik", "İstanbul", "Deniz Aksoy", "2026-08-27"),
    (12, "Hatay Solar", "Hatay", "Ali Polat", "2026-10-02"),
]

LICENSES = [
    # The three Week 2 codes keep their meaning: active, expired, turned off.
    ("7F3K-92QD", 1, "Ofis-1", "2025-03-12", "2027-03-15", 1, 30000),
    ("A1B2-C3D4", 1, "Saha laptop", "2025-03-12", "2026-10-20", 1, 24000),
    ("OLD1-PC99", 1, "Eski ofis PC", "2025-03-12", "2027-01-31", 0, 30000),
    ("K2N4-7TQA", 2, "Ofis-1", "2025-05-25", "2026-11-25", 1, 30000),
    ("K2N4-8HWB", 2, "Ofis-2", "2025-05-25", "2026-11-25", 1, 30000),
    ("K2N4-9PLC", 2, "Laptop", "2025-08-10", "2027-08-10", 1, 24000),
    ("E3M1-55XR", 3, "Ofis", "2025-07-03", "2026-07-03", 1, 30000),
    ("T4G7-21AB", 4, "Ofis", "2025-09-16", "2027-09-16", 1, 36000),
    ("T4G7-33CD", 4, "Laptop", "2025-10-01", "2026-11-15", 1, 24000),
    ("KY5B-10ZZ", 5, "Ofis", "2025-11-05", "2026-11-05", 1, 30000),   # expired yesterday
    ("MC6S-44QW", 6, "Ofis-1", "2026-01-15", "2027-01-15", 1, 36000),
    ("MC6S-45QE", 6, "Ofis-2", "2026-01-15", "2027-01-15", 1, 36000),
    ("MC6S-46QR", 6, "Ofis-3", "2026-01-15", "2027-01-15", 0, 36000),
    ("AK7E-70AA", 7, "Ofis", "2026-02-22", "2027-02-22", 1, 30000),
    ("KP8S-81BB", 8, "Ofis", "2026-03-06", "2026-12-06", 1, 24000),   # exactly 30 days left
    ("IA9P-90CC", 9, "Ofis", "2026-04-20", "2027-04-20", 1, 30000),
    ("IA9P-91DD", 9, "Laptop", "2026-04-20", "2027-04-20", 1, 24000),
    ("DG10-11EE", 10, "Ofis", "2026-06-10", "2027-06-10", 1, 30000),
    ("TM11-12FF", 11, "Ofis", "2026-08-28", "2027-08-28", 1, 36000),
    ("TM11-13GG", 11, "Şantiye laptop", "2026-09-10", "2027-09-10", 1, 24000),
]
# Customer 12 (Hatay Solar) has no license yet: needed for LEFT JOIN questions.


def build_db(path: Path = DB_FILE) -> Path:
    """(Re)create the database file from the seed data above."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", CUSTOMERS)
        conn.executemany("INSERT INTO licenses VALUES (?, ?, ?, ?, ?, ?, ?)", LICENSES)
    conn.close()
    return path


WRITE_WORDS = re.compile(r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum)\b", re.I)


def is_safe_select(sql: str) -> bool:
    """One statement, starting with SELECT or WITH, no write keywords. A first line of defence."""
    statement = sql.strip().rstrip(";").strip()
    if not statement or ";" in statement:
        return False
    if not re.match(r"^(select|with)\b", statement, re.I):
        return False
    return not WRITE_WORDS.search(statement)


def run_readonly(sql: str, path: Path = DB_FILE, max_rows: int = 1000) -> list[tuple]:
    """Execute a query on a read-only connection. The second line of defence:
    even if a write slips past is_safe_select, SQLite refuses it in mode=ro."""
    if not is_safe_select(sql):
        raise ValueError("Only a single SELECT statement is allowed")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchmany(max_rows)
    finally:
        conn.close()
