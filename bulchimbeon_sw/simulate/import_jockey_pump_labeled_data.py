"""
충압펌프(jockey) 벤치 실험 데이터를 data/수계 엣지/combined_all_labeled.csv에서
읽어 water_pump_log에 반영한다.

이 데이터는 실시간 성능시험(압력 패턴 기반 valve_state 추정)으로 얻은 게 아니라
전용 채집 스케치(pump_jockey_data_collector_v2.ino)로 물리 조건을 직접 바꿔가며
벤치에서 딴 것이므로 label_source='manual'로 기록한다.

사용법: python simulate/import_jockey_pump_labeled_data.py
"""
import csv
import sqlite3
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "server"))
from config import DB_PATH  # noqa: E402

CSV_PATH = (
    Path(__file__).parent.parent.parent
    / "data" / "수계 엣지" / "combined_all_labeled.csv"
)
NODE_ID = "pump_jockey_01"
PUMP_TYPE = "jockey"


def load_rows():
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def main():
    rows = load_rows()
    conn = sqlite3.connect(DB_PATH)
    ts = time.time()
    inserted = 0

    for row in rows:
        conn.execute(
            """INSERT INTO water_pump_log
               (ts, node_id, pump_type, rms, peak, duty_cycle, label, label_source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                ts, NODE_ID, PUMP_TYPE,
                float(row["rms_mA"]), float(row["peak_mA"]), float(row["duty_cycle"]),
                row["label"], "manual",
            ),
        )
        inserted += 1
        ts += 1.0  # 채집 순서를 시간축에 반영 (의미 없는 값, 중복 방지용)

    conn.commit()
    conn.close()

    print(f"[완료] {inserted}개 행 반영")
    label_counts = {}
    for row in rows:
        label_counts[row["label"]] = label_counts.get(row["label"], 0) + 1
    for label, count in sorted(label_counts.items()):
        print(f"  {label}: {count}개")


if __name__ == "__main__":
    main()
