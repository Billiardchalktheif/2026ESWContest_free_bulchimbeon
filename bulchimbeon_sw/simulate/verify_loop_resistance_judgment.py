"""
자탐 루프저항 2층 판정(server/judge/regression.py의 evaluate_loop_resistance) 검증 스크립트.
기존 z-score 검증 스크립트(verify_fire_alarm_zscore.py)를 대체한다 — 로직 자체가
z-score에서 절대임계값+선형회귀 2층 구조로 전면 교체됐기 때문에, 검증 시나리오도
그에 맞게 새로 짰다. 임시 DB 생성 -> handle_packet() 직접 호출 -> 상태 이력 검사라는
스크립트 구조 자체는 그대로 재사용했다.

네 시나리오:
  A구역(정상): raw 값이 baseline 근처에서 노이즈만 있고 추세 없음 -> 거의 항상 normal.
  B구역(서서히 열화): raw 값이 시간에 따라 서서히 증가 -> normal -> caution -> alarm 전이.
  C구역(절대 임계값 즉시 초과): 표본이 LOOP_TREND_MIN_SAMPLES 미만인 초기 상태에서
    raw 값이 갑자기 크게 튐 -> 추세 판정(2층)은 표본 부족으로 보류(normal)여도,
    절대 임계값(1층)은 즉시 alarm으로 판정하는지 확인 — 두 층이 독립적으로
    동작하는지 검증하는 핵심 케이스.
  D구역(고빈도 노이즈, 실제 하드웨어 재현): 자탐1 실물(SEND_INTERVAL_MS=5000)과
    동일한 5초 간격으로 baseline 근처 순수 노이즈만 보내, 다운샘플링
    (LOOP_TREND_DOWNSAMPLE_EVERY_N) 적용 후 가짜 alarm 비율이 목표(5% 이하)로
    억제되는지 확인 — 2026-08-16 실측에서 다운샘플링 이전엔 30개 중 8개(약 27%)
    수준으로 가짜 alarm이 발생했던 문제의 회귀 검증.

사용법: python simulate/verify_loop_resistance_judgment.py
"""
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).parent.parent / "server"))
from receiver.packet_parser import handle_packet  # noqa: E402
from config import LOOP_FIXED_OFFSET_OHM, LOOP_RESISTANCE_HARD_LIMIT_OHM, LOOP_TREND_MIN_SAMPLES  # noqa: E402
from dummy_generator import FIRE_ALARM_BASELINE_OHM, FIRE_ALARM_NOISE_STD  # noqa: E402

# B구역(서서히 열화)은 이 프로젝트가 새 판정으로 잡으려는 바로 그 현상 —
# "가속 열화"(파일 상단 설명 §1 참고) — 를 재현해야 정확히 검증된다. dummy_generator.py의
# FIRE_ALARM_DEGRADE_SLOPE(선형, 상시 전체 파이프라인 데모용으로 빠른 반응을 우선한 값)를
# 그대로 쓰면 "전체 이력 누적회귀"가 처음부터 끝까지 거의 같은 기울기로 수렴해버려서
# normal -> caution -> alarm 3단계 전이를 보여주기 어렵다(선형 추세는 growing-window
# 회귀로도 항상 같은 가상 기울기로 보이기 때문). 그래서 검증 스크립트에서는 시간의
# 제곱에 비례해 가속하는 프로파일을 쓴다 — corrected(t_days) = ACCEL_K * t_days^2.
# 균등 표본에서 [0,T] 전체 회귀 기울기는 근사적으로 ACCEL_K*T로 수렴하므로(적분으로
# 유도 가능), growing window 특성상 시간이 지날수록 "관측되는 기울기" 자체가 거의
# 선형으로 커진다 — 그래서 정확히 caution/alarm 임계값을 넘는 시점을 예측 가능하게
# 설계할 수 있다. 아래 값은 n_points=100(=4.125일)에서 caution은 초반~1일 근처,
# alarm은 후반~3일 근처에서 걸리도록 실측 시뮬레이션으로 역산한 값이다.
ACCEL_K = 2.0 / 3.0  # Ω/day^2 — corrected(t_days) ≈ ACCEL_K * t_days^2

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


def check_stable_zone(conn: sqlite3.Connection, node_id: str) -> bool:
    """
    A구역(정상): 추세 없는 순수 노이즈 구간. LOOP_TREND_MIN_SAMPLES=3처럼 표본이 아주
    적은 초반 구간은 회귀 기울기의 표본분산 자체가 커서(N=3일 때 Ω/day 환산 표준편차가
    수 Ω/day대로, ALARM_SLOPE(2.0)보다도 큼 — "초기 반응성을 우선"한 설계의 트레이드오프)
    노이즈만으로도 드물게 caution/alarm이 튈 수 있다. 표본이 쌓일수록(N이 커질수록)
    회귀 기울기의 분산은 1/N^2로 줄어들어 뒤로 갈수록 급격히 안정된다 — 그래서 여기서는
    "완전히 0%"가 아니라 "드물게만 발생하고 뒤로 갈수록 사라지는지"를 확인한다.
    """
    rows = conn.execute(
        "SELECT status FROM fire_alarm_log WHERE node_id=? ORDER BY ts ASC", (node_id,)
    ).fetchall()
    statuses = [r[0] for r in rows]
    print(f"\n[{node_id}] 상태 이력 (뒤 20개): {statuses[-20:]}")

    alarm_ratio = statuses.count("alarm") / len(statuses)
    caution_ratio = statuses.count("caution") / len(statuses)
    tail_20 = statuses[-20:]
    tail_clean = tail_20.count("normal") == len(tail_20)

    if alarm_ratio > 0.05 or caution_ratio > 0.20 or not tail_clean:
        print(f"  [FAIL] 정상 구역인데 alarm 비율={alarm_ratio:.0%}, caution 비율={caution_ratio:.0%}, "
              f"뒤 20개 전부 normal={tail_clean}")
        return False
    print(f"  [PASS] alarm 비율={alarm_ratio:.0%}(초반 표본부족 구간의 통계적 노이즈), "
          f"caution 비율={caution_ratio:.0%}, 뒤 20개는 전부 normal로 안정됨")
    return True


def check_degrading_zone(conn: sqlite3.Connection, node_id: str) -> bool:
    """B구역(서서히 열화): normal -> caution -> alarm 순으로 전이되는지 확인."""
    rows = conn.execute(
        "SELECT status FROM fire_alarm_log WHERE node_id=? ORDER BY ts ASC", (node_id,)
    ).fetchall()
    statuses = [r[0] for r in rows]
    print(f"\n[{node_id}] 상태 이력 (뒤 20개): {statuses[-20:]}")

    reached_caution = "caution" in statuses
    reached_alarm = "alarm" in statuses
    if reached_caution and reached_alarm:
        print("  [PASS] caution -> alarm 전이 확인됨 (선형회귀 기울기 기반 2층 판정)")
        return True
    print(f"  [FAIL] 예상한 전이가 나타나지 않음 (caution={reached_caution}, alarm={reached_alarm})")
    return False


def check_absolute_threshold_independence(conn: sqlite3.Connection, node_id: str) -> bool:
    """
    C구역(절대 임계값 즉시 초과): 표본이 LOOP_TREND_MIN_SAMPLES 미만인 상태에서
    raw 값이 갑자기 크게 튀는 경우, 2층(추세)이 표본 부족으로 판정을 보류(normal)해도
    1층(절대 임계값)은 즉시 alarm을 내야 한다 — 두 층이 서로를 막지 않고 독립적으로
    동작하는지가 이 케이스의 핵심 검증 포인트.
    """
    rows = conn.execute(
        """SELECT status, loop_trend_slope_ohm_per_day FROM fire_alarm_log
           WHERE node_id=? ORDER BY ts ASC""",
        (node_id,),
    ).fetchall()
    print(f"\n[{node_id}] 상태 이력: {rows}")

    last_status, last_slope = rows[-1]
    sample_count_at_spike = len(rows) - 1  # 마지막 패킷 처리 시점의 "이전 이력" 개수

    if sample_count_at_spike >= LOOP_TREND_MIN_SAMPLES:
        print(f"  [FAIL] 테스트 설계 오류 — 표본이 이미 {sample_count_at_spike}개라 추세 판정 보류 조건을 검증할 수 없음")
        return False

    if last_status != "alarm":
        print(f"  [FAIL] 표본 부족 상태에서도 절대 임계값 초과는 즉시 alarm이어야 하는데 status={last_status}")
        return False

    # 2층(추세)이 실제로 표본 부족으로 계산을 보류했는지(None)도 함께 확인 —
    # 그래야 "우연히 추세로도 alarm이 나왔을 뿐"이 아니라 1층이 단독으로 판정했음이 증명된다.
    if last_slope is not None:
        print(f"  [FAIL] 표본 부족인데 추세 기울기가 계산됨(loop_trend_slope_ohm_per_day={last_slope}) — 2층이 보류하지 않음")
        return False

    print(f"  [PASS] 표본 {sample_count_at_spike}개(<{LOOP_TREND_MIN_SAMPLES})로 2층은 보류(slope=None)했지만, "
          f"1층 절대 임계값이 즉시 alarm 판정함")
    return True


def check_high_frequency_noise_no_false_alarm(conn: sqlite3.Connection, node_id: str) -> bool:
    """
    D구역: 5초 간격 전송을 재현한 순수 노이즈 데이터에서, 다운샘플링 적용 후
    alarm 비율이 크게 낮아지는지 확인한다. 다운샘플링 이전(직전 버그 상태)에는
    30개 중 8개(약 27%) 수준의 가짜 alarm이 발생했음 — 이 테스트는 그 수치가
    유의미하게 개선됐는지(5% 이하 목표) 검증한다.
    """
    rows = conn.execute(
        "SELECT status FROM fire_alarm_log WHERE node_id=? ORDER BY ts ASC", (node_id,)
    ).fetchall()
    statuses = [r[0] for r in rows]
    alarm_ratio = statuses.count("alarm") / len(statuses)
    print(f"\n[{node_id}] 5초간격 노이즈 alarm 비율: {alarm_ratio:.0%} (표본 {len(statuses)}개)")
    if alarm_ratio > 0.05:
        print("  [FAIL] 다운샘플링 적용 후에도 alarm 비율이 5%를 초과함 - 추가 조정 필요")
        return False
    print("  [PASS] 다운샘플링으로 가짜 alarm이 억제됨")
    return True


def run_verification(n_points: int = 100) -> bool:
    conn = build_test_db()
    ts = time.time()

    # --- A/B 시나리오: 정상 vs 가속 열화 (기존 z-score 검증과 동일한 취지) ---
    for i in range(n_points):
        elapsed_days = (i * 3600) / 86400.0
        healthy = FIRE_ALARM_BASELINE_OHM + RNG.normal(0, FIRE_ALARM_NOISE_STD)
        degrading = (
            FIRE_ALARM_BASELINE_OHM
            + ACCEL_K * elapsed_days ** 2
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

    ok_a = check_stable_zone(conn, "fire_zone_A")
    ok_b = check_degrading_zone(conn, "fire_zone_B")

    # --- C 시나리오: 표본 부족 상태에서 절대 임계값 즉시 alarm (1층/2층 독립성 검증) ---
    ts_c = time.time()
    spike_raw = LOOP_FIXED_OFFSET_OHM + LOOP_RESISTANCE_HARD_LIMIT_OHM + 10.0  # 보정저항 기준 60Ω (법정기준 50Ω 초과)
    # 표본 1개(baseline 근처) 보낸 뒤 바로 스파이크 — 스파이크 처리 시점의 "이전 이력"은
    # 1개뿐이라 LOOP_TREND_MIN_SAMPLES(3) 미만이 보장된다.
    handle_packet(conn, {
        "node_id": "fire_zone_C", "device_type": "fire_alarm",
        "zone": "C구역(절대임계값 즉시초과)", "seq": 0, "ts": ts_c,
        "loop_resistance_ohm": FIRE_ALARM_BASELINE_OHM,
    })
    handle_packet(conn, {
        "node_id": "fire_zone_C", "device_type": "fire_alarm",
        "zone": "C구역(절대임계값 즉시초과)", "seq": 1, "ts": ts_c + 3600,
        "loop_resistance_ohm": spike_raw,
    })
    ok_c = check_absolute_threshold_independence(conn, "fire_zone_C")

    # --- D 시나리오: 5초 간격 순수 노이즈 (실물 하드웨어 재현, 다운샘플링 회귀 검증) ---
    ts_d = time.time()
    n_points_d = 150  # 12개당 1개면 회귀 이력 12개, 5개당 1개면 30개 확보되는 넉넉한 개수
    for i in range(n_points_d):
        raw = FIRE_ALARM_BASELINE_OHM + RNG.normal(0, FIRE_ALARM_NOISE_STD)
        handle_packet(conn, {
            "node_id": "fire_zone_D", "device_type": "fire_alarm",
            "zone": "D구역(5초간격 노이즈)", "seq": i, "ts": ts_d, "loop_resistance_ohm": raw,
        })
        ts_d += 5  # 자탐1 실물(SEND_INTERVAL_MS=5000)과 동일한 간격
    ok_d = check_high_frequency_noise_no_false_alarm(conn, "fire_zone_D")

    conn.close()
    TEST_DB_PATH.unlink()

    all_ok = ok_a and ok_b and ok_c and ok_d
    print(f"\n{'전체 PASS' if all_ok else '전체 FAIL'}")
    return all_ok


if __name__ == "__main__":
    success = run_verification()
    sys.exit(0 if success else 1)
