"""
1회성 마이그레이션 — water_pump_log에 flow_lpm 컬럼 추가.
운영 DB(실측 데이터 누적됨)에 안전하게 적용하기 위해 PRAGMA로 기존 컬럼 여부를
확인한 뒤 없으면만 ALTER TABLE로 추가한다 — 재실행해도 안전.

사용법: python server/storage/migrate_add_flow_lpm.py [db경로]
"""
import sqlite3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from config import DB_PATH as DEFAULT_DB_PATH  # noqa: E402


def migrate(db_path: Path = DEFAULT_DB_PATH):
    conn = sqlite3.connect(db_path)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(water_pump_log)")}
    if "flow_lpm" not in existing:
        conn.execute("ALTER TABLE water_pump_log ADD COLUMN flow_lpm REAL")
        print("컬럼 추가됨: water_pump_log.flow_lpm")
    else:
        print("이미 존재 - 건너뜀: water_pump_log.flow_lpm")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH
    migrate(path)
