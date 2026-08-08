"""
DB 초기화: schema.sql을 읽어 SQLite DB 파일을 생성한다.
v7 폴더 재편으로 db/init_db.py에서 이 위치(server/storage/db.py)로 옮겨왔다 —
함수명/로직은 그대로 유지했고, 경로만 server/config.py의 DB_PATH/SCHEMA_PATH를 쓰도록 바꿨다.

사용법: python server/storage/db.py [db경로]
"""
import sqlite3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from config import DB_PATH as DEFAULT_DB_PATH  # noqa: E402
from config import SCHEMA_PATH  # noqa: E402


def init_db(db_path: Path = DEFAULT_DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)  # storage/ 디렉터리가 없으면 생성
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    print(f"DB 초기화 완료: {db_path}")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH
    init_db(path)
