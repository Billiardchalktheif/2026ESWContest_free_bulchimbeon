"""
자탐2(광전식) 5클래스(normal/smoking/incense/heat/fire) 실측 데이터를
data/광전식 엣지/photoelectric_all_classes_combined.csv에서 읽어 fire_alarm_log에 반영한다.

CSV는 세션당 1행 요약값(상승률 % 기준)이다 — DB 컬럼(mq2_raw/humidity_pct/temp_rise_rate)
이름은 원시 순간값 기준으로 설계돼 있지만, 지금은 상승률 값을 그대로 넣는 근사 매핑이다.
학습에는 문제없지만, 실시간 추론 경로(packet_parser.py)와 feature 성격이 다르다는 점을
결선 전에 반드시 재확인할 것 (지시서 상단 참고).

사용법: python simulate/import_photoelectric_labeled_data.py
"""
import csv
import sqlite3
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "server"))
from config import DB_PATH  # noqa: E402

sys.path.append(str(Path(__file__).parent))
from dummy_generator import FIRE_ALARM_BASELINE_OHM  # noqa: E402

CSV_PATH = (
    Path(__file__).parent.parent.parent
    / "data" / "광전식 엣지" / "photoelectric_all_classes_combined.csv"
)
NODE_ID = "fire_zone_photoelectric_01"
ZONE = "광전식구역"
ZONE_TYPE = "photoelectric"

# 이상치로 표시된(OUTLIER_CHECK) 세션 제외 여부 — 원인 미확인 상태.
# True로 두면 일단 포함해서 학습(권장: 표본 수 확보), 재검토 후 필요시 False로.
INCLUDE_FLAGGED_OUTLIERS = True


def load_rows():
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def main():
    rows = load_rows()
    skipped_outliers = 0
    inserted = 0

    conn = sqlite3.connect(DB_PATH)
    ts = time.time()

    for row in rows:
        is_outlier = "OUTLIER_CHECK" in (row.get("note") or "")
        if is_outlier and not INCLUDE_FLAGGED_OUTLIERS:
            skipped_outliers += 1
            continue

        conn.execute(
            """INSERT INTO fire_alarm_log
               (ts, node_id, zone, zone_type, loop_resistance_ohm,
                temp_rise_rate, mq2_raw, humidity_pct, label)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                ts, NODE_ID, ZONE, ZONE_TYPE, FIRE_ALARM_BASELINE_OHM,
                float(row["temp_rise_c"]),
                float(row["mq2_rise_pct"]),
                float(row["humidity_change_pct"]),
                row["label"],
            ),
        )
        inserted += 1
        ts += 1.0  # 세션 순서를 시간축에 반영 (동일 ts 중복 방지용, 의미는 없음)

    conn.commit()
    conn.close()

    print(f"[완료] {inserted}개 세션 반영 (이상치 제외: {skipped_outliers}개)")
    label_counts = {}
    for row in rows:
        if not INCLUDE_FLAGGED_OUTLIERS and "OUTLIER_CHECK" in (row.get("note") or ""):
            continue
        label_counts[row["label"]] = label_counts.get(row["label"], 0) + 1
    for label, count in sorted(label_counts.items()):
        print(f"  {label}: {count}개")


if __name__ == "__main__":
    main()
