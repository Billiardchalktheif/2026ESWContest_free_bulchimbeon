"""
1회성 마이그레이션 — 자탐1 온도상승률 판정 + 점검모드 컬럼/테이블 추가.
운영 DB(실측 데이터 누적됨)에 안전하게 적용하기 위해 PRAGMA로 기존 컬럼 여부를
확인한 뒤 없는 것만 ALTER TABLE로 추가한다 — 재실행해도 안전(이미 있으면 건너뜀).
schema.sql의 CREATE TABLE IF NOT EXISTS는 신규 설치에만 적용되고 기존 테이블은
건드리지 않으므로, 이미 운영 중인 DB는 이 스크립트로 별도 반영해야 한다.

사용법: python server/storage/migrate_add_differential_test_mode.py [db경로]
"""
import sqlite3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from config import DB_PATH as DEFAULT_DB_PATH  # noqa: E402

NEW_COLUMNS = {
    "temp_raw_adc": "INTEGER",
    "temp_v": "REAL",
    "boot_elapsed_ms": "INTEGER",
    "temp_status": "TEXT",  # DEFAULT 없음 — NULL(판정 안 됨) vs 'normal'(판정 결과)을 구분해야 함
    "test_mode": "INTEGER DEFAULT 0",
}


def migrate(db_path: Path = DEFAULT_DB_PATH):
    conn = sqlite3.connect(db_path)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(fire_alarm_log)")}
    for col, col_type in NEW_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE fire_alarm_log ADD COLUMN {col} {col_type}")
            print(f"컬럼 추가됨: fire_alarm_log.{col}")
        else:
            print(f"이미 존재 - 건너뜀: fire_alarm_log.{col}")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS test_mode_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            device_type TEXT NOT NULL,
            zone TEXT NOT NULL,
            node_id TEXT,
            judged_status TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()
    print(f"마이그레이션 완료: {db_path}")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH
    migrate(path)
