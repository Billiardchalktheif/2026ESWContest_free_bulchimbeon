"""
자탐 루프저항 2층 판정(server/judge/regression.py의 evaluate_loop_resistance) 검증 스크립트.
기존 z-score 검증 스크립트(verify_fire_alarm_zscore.py)를 대체한다 — 로직 자체가
z-score에서 절대임계값+선형회귀 2층 구조로 전면 교체됐기 때문에, 검증 시나리오도
그에 맞게 새로 짰다. 임시 DB 생성 -> handle_packet() 직접 호출 -> 상태 이력 검사라는
스크립트 구조 자체는 그대로 재사용했다.

다섯 시나리오:
  A구역(정상, 1시간 간격): raw 값이 baseline 근처에서 노이즈만 있고 추세 없음
    -> 거의 항상 normal. 장기(달력 단위) 운영에서의 안정성을 본다.
  B구역(서서히 열화, 5초 간격): 자탐1 실물과 동일한 5초 간격으로, 시연 중 사람이
    포텐셔미터를 손으로 돌리는 상황을 흉내낸 가속 프로파일을 보낸다 ->
    normal -> caution -> alarm 순으로 3분 내외에 전이되는지 확인.
  C구역(절대 임계값 즉시 초과): 표본이 LOOP_TREND_MIN_SAMPLES 미만인 초기 상태에서
    raw 값이 갑자기 크게 튐 -> 추세 판정(2층)은 표본 부족으로 보류(normal)여도,
    절대 임계값(1층)은 즉시 alarm으로 판정하는지 확인 — 두 층이 독립적으로
    동작하는지 검증하는 핵심 케이스.
  D구역(고빈도 노이즈, 실제 하드웨어 재현): 자탐1 실물(SEND_INTERVAL_MS=5000)과
    동일한 5초 간격으로 baseline 근처 순수 노이즈만 보내, 다운샘플링
    (LOOP_TREND_DOWNSAMPLE_EVERY_N)과 재조정된 LOOP_TREND_ALARM_SLOPE/
    CAUTION_SLOPE(config.py, 2026-08-16) 적용 후 가짜 alarm 비율이 목표(5% 이하)로
    억제되는지 확인.

  B/D 둘 다 5초 간격을 쓰는 이유: LOOP_TREND_ALARM_SLOPE/CAUTION_SLOPE는 D시나리오의
  노이즈 통계를 기반으로 재조정됐고, 이 값이 "진짜 열화 신호"도 여전히 합리적 시간
  내에 잡아내는지는 반드시 같은 시간축(5초 간격)에서 검증해야 의미가 있다 — A처럼
  1시간 간격으로 두면 노이즈 증폭 배율 자체가 완전히 달라서(§ config.py 주석 참고)
  같은 임계값을 놓고 비교할 수 없다.

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

# A구역(정상, 1시간 간격) 열화 없음 — 추세 프로파일 불필요, RNG 노이즈만 사용.
#
# B구역(서서히 열화, 5초 간격)은 이 프로젝트가 새 판정으로 잡으려는 바로 그 현상 —
# "가속 열화"(파일 상단 설명 §1 참고) — 를 재현해야 정확히 검증된다. 선형(등속) 램프를
# 쓰면 회귀가 창(window) 전체에서 거의 같은 기울기로 수렴해버려서 normal -> caution ->
# alarm 3단계 전이 없이 노이즈 여부에 따라 곧장 alarm으로 튀거나(램프가 크면) 계속
# normal이거나(작으면) 둘 중 하나로 쏠린다 — caution 단계를 거치는 "점진적" 전이를
# 보여주려면 시간의 제곱에 비례해 가속하는 프로파일이 필요하다:
#   corrected(t_sec) = B_ACCEL_K * t_sec^2  (사람이 포텐셔미터를 서서히 더 빠르게
#   돌리는 상황을 흉내낸 것 — 처음엔 완만하다가 점점 변화폭이 커짐)
# 아래 값은 LOOP_TREND_ALARM_SLOPE=700/CAUTION_SLOPE=230(2026-08-16 재조정, config.py
# 참고) 기준으로, caution은 70~120초, alarm은 120~270초 부근에서 안정적으로(15개
# 시드 중 15개 전부 normal->caution->alarm 순서 확인) 전이되도록 실측 시뮬레이션으로
# 역산한 값이다 — 3분 내외의 시연 시간 예산 안에 들어온다.
B_ACCEL_K = 0.00004  # Ω/sec^2 — corrected(t_sec) ≈ B_ACCEL_K * t_sec^2

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
    A구역(정상): 추세 없는 순수 노이즈 구간(1시간 간격이라 D보다 훨씬 느긋한 시간축).
    LOOP_TREND_MIN_SAMPLES=3처럼 표본이 아주 적은 초반 구간은 회귀 기울기의 표본분산
    자체가 커서 노이즈만으로도 드물게 caution/alarm이 튈 수 있다 — "초기 반응성을
    우선"한 설계의 트레이드오프. 표본이 쌓일수록(N이 커질수록) 회귀 기울기의 분산은
    1/N^2로 줄어들어 뒤로 갈수록 급격히 안정된다 — 그래서 여기서는 "완전히 0%"가
    아니라 "드물게만 발생하고 뒤로 갈수록 사라지는지"를 확인한다.
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
    """
    B구역(서서히 열화, 5초 간격): normal -> caution -> alarm 순서로(역순 아님) 전이
    되는지, 그리고 caution이 alarm보다 먼저 나타나는지까지 확인한다. 단순히 "둘 다
    어딘가에 있다"만 보면, 노이즈로 먼저 alarm이 튀었다가 caution으로 돌아오는
    비정상적인 흐름도 통과시켜버리므로 순서까지 확인한다.
    """
    rows = conn.execute(
        "SELECT status FROM fire_alarm_log WHERE node_id=? ORDER BY ts ASC", (node_id,)
    ).fetchall()
    statuses = [r[0] for r in rows]
    print(f"\n[{node_id}] 상태 이력: {statuses}")

    caution_idx = statuses.index("caution") if "caution" in statuses else None
    alarm_idx = statuses.index("alarm") if "alarm" in statuses else None
    if caution_idx is not None and alarm_idx is not None and caution_idx < alarm_idx:
        print(f"  [PASS] caution(샘플 {caution_idx}, {caution_idx*5}초) -> "
              f"alarm(샘플 {alarm_idx}, {alarm_idx*5}초) 순서로 전이 확인됨")
        return True
    print(f"  [FAIL] 예상한 순서의 전이가 나타나지 않음 (caution_idx={caution_idx}, alarm_idx={alarm_idx})")
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
    D구역: 5초 간격 전송을 재현한 순수 노이즈 데이터에서 alarm 비율이 목표(5% 이하)로
    억제되는지 확인한다. 다운샘플링(LOOP_TREND_DOWNSAMPLE_EVERY_N)만으로는 이 목표를
    못 채웠고(2026-08-16 1차 검증, 20~60% 수준), LOOP_TREND_ALARM_SLOPE/CAUTION_SLOPE
    재조정(config.py, 같은 날짜)까지 함께 적용한 뒤에야 통과했다 — 이 테스트는 그
    재조정이 여전히 유효한지 계속 지켜보는 회귀(regression) 검증 역할을 한다.
    """
    rows = conn.execute(
        "SELECT status FROM fire_alarm_log WHERE node_id=? ORDER BY ts ASC", (node_id,)
    ).fetchall()
    statuses = [r[0] for r in rows]
    alarm_ratio = statuses.count("alarm") / len(statuses)
    print(f"\n[{node_id}] 5초간격 노이즈 alarm 비율: {alarm_ratio:.0%} (표본 {len(statuses)}개)")
    if alarm_ratio > 0.05:
        print("  [FAIL] alarm 비율이 5%를 초과함 - LOOP_TREND_ALARM_SLOPE 추가 조정 필요")
        return False
    print("  [PASS] alarm 오탐이 목표 수준으로 억제됨")
    return True


def run_verification(n_points: int = 100) -> bool:
    conn = build_test_db()

    # --- A 시나리오: 정상, 1시간 간격 (장기 운영 안정성) ---
    ts_a = time.time()
    for i in range(n_points):
        healthy = FIRE_ALARM_BASELINE_OHM + RNG.normal(0, FIRE_ALARM_NOISE_STD)
        handle_packet(conn, {
            "node_id": "fire_zone_A", "device_type": "fire_alarm",
            "zone": "A구역(정상)", "seq": i, "ts": ts_a, "loop_resistance_ohm": healthy,
        })
        ts_a += 3600  # 1시간 간격 가정
    ok_a = check_stable_zone(conn, "fire_zone_A")

    # --- B 시나리오: 서서히 열화, 5초 간격 (실물 하드웨어와 동일 시간축 — 새로 조정된
    # LOOP_TREND_ALARM_SLOPE/CAUTION_SLOPE가 진짜 열화 신호도 잡아내는지 확인) ---
    ts_b = time.time()
    n_points_b = 80  # 최대 400초(6.7분) — B_ACCEL_K 기준으로 넉넉히 alarm까지 도달하는 길이
    for i in range(n_points_b):
        t_sec = i * 5
        degrading = FIRE_ALARM_BASELINE_OHM + B_ACCEL_K * (t_sec ** 2) + RNG.normal(0, FIRE_ALARM_NOISE_STD)
        handle_packet(conn, {
            "node_id": "fire_zone_B", "device_type": "fire_alarm",
            "zone": "B구역(열화재현)", "seq": i, "ts": ts_b, "loop_resistance_ohm": degrading,
        })
        ts_b += 5  # 자탐1 실물(SEND_INTERVAL_MS=5000)과 동일한 간격
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

    # --- D 시나리오: 5초 간격 순수 노이즈 (실물 하드웨어 재현, 다운샘플링+재조정 임계값 검증) ---
    # 150개(750초) 정도로는 표본 수가 적어 alarm 비율 측정 자체의 시드별 편차가 커서
    # (25회 반복 시뮬레이션에서 150개 기준 최대 6.7% vs 300개 기준 최대 3.3%로 확인,
    # 2026-08-16) 300개로 늘려 측정을 더 안정적으로 만들었다.
    ts_d = time.time()
    n_points_d = 300
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
