"""
자탐 z-score 판정 로직(server/judge/regression.py) 검증 스크립트.

목적: A구역(정상)은 반복될수록 계속 status='normal'을 유지해야 하고,
B구역(열화재현)은 저항이 서서히 올라가면서 status가
normal -> caution -> alarm 순으로 전이되어야 한다.
실제 UDP 통신 없이 handle_packet()을 직접 호출해 빠르게 검증한다
(별도의 임시 DB를 만들어 쓰므로 실제 운영 DB에는 영향 없음).

사용법: python simulate/verify_fire_alarm_zscore.py
"""
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).parent.parent / "server"))
from receiver.packet_parser import handle_packet  # noqa: E402
from dummy_generator import (  # noqa: E402
    FIRE_ALARM_BASELINE_OHM, FIRE_ALARM_DEGRADE_SLOPE, FIRE_ALARM_NOISE_STD,
)

TEST_DB_PATH = Path(__file__).parent.parent / "server" / "storage" / "_test_fire_alarm.db"
SCHEMA_PATH = Path(__file__).parent.parent / "server" / "storage" / "schema.sql"

RNG = np.random.default_rng(1)


def build_test_db() -> sqlite3.Connection:
    """검증 전용 임시 DB를 새로 만든다 (운영 DB와 분리)"""
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    conn = sqlite3.connect(TEST_DB_PATH)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    return conn


def check_zone(conn: sqlite3.Connection, node_id: str, expect_stable: bool) -> bool:
    rows = conn.execute(
        "SELECT status FROM fire_alarm_log WHERE node_id=? ORDER BY ts ASC", (node_id,)
    ).fetchall()
    statuses = [r[0] for r in rows]
    print(f"\n[{node_id}] 상태 이력 (뒤 20개): {statuses[-20:]}")

    if expect_stable:
        # v3: 지속성(persistence) 조건 도입 후 — alarm(3회 연속)은 사실상 0%로 억제되어야
        # 한다(이게 v3에서 지적한 "신뢰도" 문제의 핵심). caution은 상대적으로 관대한
        # 다수결(2/3) 조건이라 정상 구역에서도 어느 정도(~10%) 나올 수 있음을 인정한다
        # (caution은 "주의 관찰" 수준이지 오경보가 아니므로 alarm만큼 엄격할 필요는 없음).
        alarm_ratio = statuses.count("alarm") / len(statuses)
        caution_ratio = statuses.count("caution") / len(statuses)
        if alarm_ratio > 0.0 or caution_ratio > 0.10:
            print(f"  [FAIL] 정상 구역인데 alarm 비율={alarm_ratio:.0%}, caution 비율={caution_ratio:.0%}")
            return False
        print(f"  [PASS] alarm 비율={alarm_ratio:.0%}, caution 비율={caution_ratio:.0%} (지속성 조건으로 오경보 억제됨)")
        return True
    else:
        reached_caution = "caution" in statuses
        reached_alarm = "alarm" in statuses
        if reached_caution and reached_alarm:
            print("  [PASS] caution -> alarm 전이 확인됨")
            return True
        print(f"  [FAIL] 예상한 전이가 나타나지 않음 (caution={reached_caution}, alarm={reached_alarm})")
        return False


def run_verification(n_points: int = 100) -> bool:
    conn = build_test_db()
    ts = time.time()
    for i in range(n_points):
        healthy = FIRE_ALARM_BASELINE_OHM + RNG.normal(0, FIRE_ALARM_NOISE_STD)
        degrading = (
            FIRE_ALARM_BASELINE_OHM
            + i * FIRE_ALARM_DEGRADE_SLOPE
            + RNG.normal(0, FIRE_ALARM_NOISE_STD)
        )
        handle_packet(conn, {
            "node_id": "fire_zone_A", "device_type": "fire_alarm",
            "zone": "A구역(정상)", "seq": i, "ts": ts, "loop_resistance_ohm": healthy,
        })
        handle_packet(conn, {
            "node_id": "fire_zone_B", "device_type": "fire_alarm",
            "zone": "B구역(열화재현)", "seq": i, "ts": ts, "loop_resistance_ohm": degrading,
        })
        ts += 3600  # 1시간 간격 가정

    ok_a = check_zone(conn, "fire_zone_A", expect_stable=True)
    ok_b = check_zone(conn, "fire_zone_B", expect_stable=False)

    conn.close()
    TEST_DB_PATH.unlink()

    print(f"\n{'전체 PASS' if (ok_a and ok_b) else '전체 FAIL'}")
    return ok_a and ok_b


if __name__ == "__main__":
    success = run_verification()
    sys.exit(0 if success else 1)
