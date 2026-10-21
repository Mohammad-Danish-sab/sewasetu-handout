"""Create the portal tables without importing or changing citizen records."""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if __package__:
    from ..db import connect_db
else:
    sys.path.insert(0, str(APP_DIR))
    from db import connect_db


def main() -> int:
    schema = (APP_DIR / "schema.sql").read_text(encoding="utf-8")
    connection = connect_db()
    cursor = connection.cursor()
    try:
        cursor.execute(schema)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    print("Portal schema is ready. No seed data was imported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
