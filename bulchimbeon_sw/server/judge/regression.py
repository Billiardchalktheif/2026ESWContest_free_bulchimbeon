"""
"추세 기반 통계 판정" 모듈 — 자탐 루프저항 2층 판정 + 가스계/유도등 선형회귀.
v7 폴더 재편으로 anomaly_detection.py와 regression_forecast.py를 이 파일 하나로
합쳤다 — 둘 다 "AI가 아니라 통계로 서서히 진행되는 추세를 잡아낸다"는 성격이 같기
때문. 가스계/유도등 함수명은 기존 그대로 유지했다(evaluate_gas/evaluate_evac_light/
evaluate_evac_discharge_capacity). 자탐 판정은 evaluate_fire_alarm(z-score)을
폐기하고 evaluate_loop_resistance(2층 구조)로 전면 교체했다.

=== 자탐 루프저항 2층 판정 (evaluate_loop_resistance) ===
배경: 기존 z-score(이동평균 기반) 판정은, baseline 자체가 서서히 오르는 저항값을
계속 따라가면서(baseline이 최신값을 뒤쫓듯 같이 상승) 진짜 "가속 열화"를 정상
범위로 오판하는 문제가 실측 검증 중 발견됐다. baseline이 최근 이력의 평균인 이상,
느리게 진행되는 추세 자체를 baseline이 흡수해버리는 건 이동평균 방식의 구조적
한계라 임계값 재조정만으로는 못 고친다.

역할 재정립: 급성 단선/합선은 실제 화재수신기가 도통시험으로 이미 실시간 감시
중이다 — 상위 SW(이 프로젝트)가 같은 역할을 또 할 필요는 없다. 대신 상위 SW는
① 법정기준(50Ω) 초과 여부(절대 임계값, 1층)와 ② 그 이전 단계의 서서히 진행되는
열화 추세(선형회귀 기울기, 2층)를 보는 2층 구조로 역할을 재정립했다:

  1층 (check_absolute_threshold): 보정된 저항값이 법정기준 50Ω을 "초과"하면 즉시
    alarm. 표본 개수와 무관하게 항상 평가된다 — 급한 이상은 이력이 쌓이길
    기다리지 않고 바로 잡아야 하기 때문.
  2층 (check_resistance_trend): 최근 이력(최대 LOOP_TREND_HISTORY_LIMIT=20개)에
    선형회귀(_fit_slope_intercept 재사용, 가스계/유도등과 동일 함수)를 적용해 Ω/day
    단위 기울기를 구하고, 그 기울기가 LOOP_TREND_ALARM_SLOPE/LOOP_TREND_CAUTION_SLOPE를
    넘는지로 caution/alarm을 가른다. 가스계/유도등과 달리 전체 이력이 아니라 최근
    구간만 쓰는 이유: 오래전에 한 번 튄 스파이크가 전체 이력에 계속 남아 있으면
    시간이 지나 이미 정상으로 돌아온 뒤에도 그 스파이크가 기울기를 계속 왜곡한다.
    표본이 LOOP_TREND_MIN_SAMPLES 미만이면 판정을 보류한다(normal 유지) — 초기
    구간에 불안정한 기울기로 오경보를 내지 않기 위함이다.
  최종 status는 두 층 중 더 심각한 쪽(severity 최댓값)을 채택한다 — 1층과 2층은
  독립적으로 평가되며, 어느 한쪽이라도 alarm이면 최종 alarm이다.

⚠️ 회로 스케일 보정(LOOP_FIXED_OFFSET_OHM, config.py): 실제 조립된 회로
(fire_alarm_differential_node.ino의 measureLoopResistanceOhm())가 반환하는 raw
값은 R_loop 고정저항(안전 전류 확보 목적으로 넣은 값) + 가변저항(열화 시뮬레이션용)의
합이라 253~1413Ω 범위로 나온다 — 법정기준(50Ω)과 스케일이 완전히 다르다. 회로/
펌웨어는 그대로 두고, 이 판정 함수 내부에서만 raw 값에서 고정저항분을 빼 "가변접점만의
순수 저항"을 판정에 사용한다. 가스계의 공병중량 차감(_agent_loss_pct, v7 버그수정)과
정확히 같은 패턴이다 — raw 센서값은 손대지 않고 저장하고, 판정 시점에만 알려진 고정
오프셋을 보정한다.

적용 범위: 자탐1(차동식)/자탐2(광전식) 공통 — 루프저항 회로가 두 구역 다 동일 설계라
기존 z-score도 공통이었고, 이번 교체도 두 구역 다 같이 적용한다. 자탐2의 비화재보
판별 AI(온도/가스/습도 기반, server/judge/classify.py)는 완전히 별개 로직이라
이번 변경과 무관하다.

배치/스케줄러 없음: evaluate_gas/evaluate_evac_light와 동일하게 "패킷 도착 시마다
전체 이력을 재조회해 즉석 회귀"하는 패턴을 그대로 재사용한다 — 별도 캐시 테이블이나
배치 스케줄러는 만들지 않았다.

주의: 여기서도 AI/ML이 아니라 순수 통계(절대 임계값 비교 + 선형회귀)다.
     자탐 설비는 판정 근거가 명확히 설명 가능해야 하므로 이 방식을 유지한다.

=== 가스계/유도등 선형회귀 (evaluate_gas / evaluate_evac_light / evaluate_evac_discharge_capacity) ===
배경: 이 두 설비는 열화가 "서서히, 거의 직선적으로" 진행된다는 공통점이 있다.
     가스계는 자연 누출로 중량이 서서히 줄고, 유도등 배터리는 방전으로
     전압이 서서히 줄어든다. 복잡한 모델 없이 최근 시계열에 1차 선형회귀
     (기울기)만 적용해도 "언제 임계치에 도달할지"를 충분히 실용적으로 예측할 수 있다.
     (설계 원칙: AI는 수계 파형 분류 + 자탐2 비화재보 판별 두 곳에만 적용 — 이 둘은
      통계적 회귀로 충분)

가스계: 초기중량 대비 임계 손실률(gas_type별로 다름) 도달 시점까지 남은 일수를 예측.

유도등: v3에서 "서로 다른 두 질문"을 명확히 분리했다 (예전엔 이 둘을 섞어서 버그가 났었음).
  1) predicted_days_to_replace: 평상시 전압의 느린 달력 추세로 "배터리가 노후화로 언제
     교체 필요해지는가"를 추정 (evaluate_evac_light, 단위: 일)
  2) estimated_discharge_min: 데모모드 방전시험 구간에서 실제로 전압이 떨어지는 짧은 구간의
     기울기로 "지금 정전나면 몇 분 버티는가"를 추정하고, 법정 최소 작동시간(20분/60분)
     미달이면 status='warning' (evaluate_evac_discharge_capacity, 단위: 분)

가스계 임계값 출처별 차이(§4): 소방시설 점검관리 매뉴얼 1권은 CO2 중량손실 10%
(예외 조항 있음), 2권은 5%로 서로 다르고 팀 확인 대기 중이다. 확정 전까지는
보수적으로 낮은 값(5%)을 기본으로 쓴다.

v7 버그수정 — 공병중량 미차감: 예전엔 총중량 그대로 손실률을 계산했다. 미니어처처럼
용기 자체 무게가 약제 무게에 비해 비중이 크면, 총중량 기준 계산은 실제 약제 손실률을
과소평가한다. 그래서 "약제중량"(총중량 - 공병중량) 기준으로 바꿨다 —
_agent_loss_pct() 참고. empty_container_weight_g는 캘리브레이션 시 1회 실측해서 넣는
값이며 config.py의 DEFAULT_EMPTY_CONTAINER_WEIGHT_G에서 가져온다. **반드시 실측
캘리브레이션이 필요** — 미실측(기본값 0.0) 상태면 예전과 동일하게 총중량 기준으로
계산되어 손실률이 과소평가된다.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).parent.parent))
from config import (  # noqa: E402
    DEFAULT_EMPTY_CONTAINER_WEIGHT_G, DEFAULT_GAS_TYPE, GAS_LOSS_THRESHOLD_PCT,
    LOOP_FIXED_OFFSET_OHM, LOOP_RESISTANCE_HARD_LIMIT_OHM,
    LOOP_TREND_ALARM_SLOPE, LOOP_TREND_CAUTION_SLOPE, LOOP_TREND_MIN_SAMPLES,
)
from dispatch.lcd_buzzer_output import trigger_alert  # noqa: E402

MIN_POINTS = 5                    # 회귀에 필요한 최소 데이터 개수 (미만이면 예측 보류)

EVAC_DISCHARGE_THRESHOLD_V = 0.3     # 유도등 배터리 방전 임계전압 (실측 후 재조정 가능) + 실측으로 임계값 0.3V로 지정
EVAC_MIN_DISCHARGE_MIN = 20          # 법정 예비전원 최소 작동시간(일반) — §4
EVAC_MIN_DISCHARGE_MIN_EXTENDED = 60  # 11층 이상/지하 대형시설 등 — 참고용 옵션 상수
MIN_DISCHARGE_TEST_POINTS = 3         # 실측 방전시험 구간에서 기울기를 신뢰하기 위한 최소 샘플 수


# ---------------------------------------------------------------------------
# 자탐 루프저항 2층 판정
# ---------------------------------------------------------------------------
SEVERITY_RANK = {"normal": 0, "caution": 1, "alarm": 2}


LOOP_TREND_HISTORY_LIMIT = 20  # 회귀에 쓸 최근 표본 개수 상한 — 아래 함수 설명 참고


def _fetch_loop_resistance_history(conn, node_id: str, exclude_row_id: int):
    """
    해당 노드의 최신 행을 제외한 이력 중 "최근 LOOP_TREND_HISTORY_LIMIT개"만 (ts,
    raw_resistance_ohm) 시간 오름차순으로 반환한다. evaluate_gas/evaluate_evac_light는
    전체 이력을 다 쓰지만(캐시 없음 — 파일 상단 "배치/스케줄러 없음" 설명 참고),
    자탐은 최근 구간만 쓰도록 다르게 뒀다 — 오래전에 한 번 튄 스파이크가 전체 이력에
    계속 남아 있으면, 시간이 아무리 지나도 그 스파이크가 회귀 기울기를 계속 왜곡하기
    때문이다(전체 이력 회귀는 "지금은 이미 정상으로 돌아왔는데 과거 스파이크 때문에
    기울기가 여전히 커 보이는" 상황을 못 걷어낸다). ORDER BY ts DESC LIMIT으로 최근
    것만 뽑은 뒤 다시 오름차순으로 뒤집는다 — LOOP_TREND_MIN_SAMPLES(3)는 이 함수가
    반환하는 개수에 대해 그대로 적용되므로 바뀌지 않는다.
    """
    cur = conn.execute(
        """SELECT ts, loop_resistance_ohm FROM fire_alarm_log
           WHERE node_id = ? AND id != ? ORDER BY ts DESC LIMIT ?""",
        (node_id, exclude_row_id, LOOP_TREND_HISTORY_LIMIT),
    )
    rows = cur.fetchall()
    rows.reverse()
    return rows


def check_absolute_threshold(corrected_resistance_ohm: float):
    """1층: 법정 절대 임계값 검증. corrected_resistance_ohm > 50.0 이면 alarm."""
    if corrected_resistance_ohm > LOOP_RESISTANCE_HARD_LIMIT_OHM:
        return "alarm", f"법정기준({LOOP_RESISTANCE_HARD_LIMIT_OHM:.0f}Ω) 초과 - 보정저항 {corrected_resistance_ohm:.1f}Ω"
    return "normal", None


def check_resistance_trend(conn, node_id: str, exclude_row_id: int):
    """
    2층: 최근 이력(최대 LOOP_TREND_HISTORY_LIMIT개, 현재 행 제외)에 선형회귀(_fit_slope_intercept 재사용)를 적용해
    기울기(Ω/day)를 산출한다. raw 이력값에서 LOOP_FIXED_OFFSET_OHM을 뺀 뒤 회귀 —
    상수 오프셋이라 기울기 자체는 영향받지 않지만, 일관성을 위해 보정된 스케일로
    통일한다.

    표본 수가 LOOP_TREND_MIN_SAMPLES 미만이면 판정을 보류한다(초기 구간에 불안정한
    기울기로 오경보를 내지 않기 위함) — (None, None, "normal", None) 반환.
    """
    history = _fetch_loop_resistance_history(conn, node_id, exclude_row_id)
    if len(history) < LOOP_TREND_MIN_SAMPLES:
        return None, None, "normal", None

    ts_values = [h[0] for h in history]
    corrected_values = [h[1] - LOOP_FIXED_OFFSET_OHM for h in history]
    slope_per_sec, _ = _fit_slope_intercept(ts_values, corrected_values)
    slope_per_day = slope_per_sec * 86400.0

    if slope_per_day >= LOOP_TREND_ALARM_SLOPE:
        status = "alarm"
    elif slope_per_day >= LOOP_TREND_CAUTION_SLOPE:
        status = "caution"
    else:
        status = "normal"

    rttf_days = None
    if slope_per_day > 0:
        latest_corrected = corrected_values[-1]
        rttf_days = (LOOP_RESISTANCE_HARD_LIMIT_OHM - latest_corrected) / slope_per_day

    reason = None
    if status != "normal":
        reason = f"열화 추세 감지 - 기울기 {slope_per_day:.2f}Ω/day"

    return slope_per_day, rttf_days, status, reason


def evaluate_loop_resistance(conn, node_id: str, row_id: int, raw_resistance_ohm: float, zone: str = None):
    """
    자탐 루프저항 최종 판정(2층 구조 통합). 자탐1(차동식)/자탐2(광전식) 공통 호출.
    raw_resistance_ohm은 ESP32가 보낸 그대로의 값(고정저항 오프셋 미차감) — DB에도
    이 raw 값이 이미 INSERT되어 있는 상태에서 호출된다(receiver/packet_parser.py 참고).

    1층(절대 임계값)과 2층(추세)은 독립적으로 평가하고, 더 심각한 쪽(severity 최댓값,
    SEVERITY_RANK)을 최종 status로 채택한다 — 급성 이상(1층)과 서서히 진행되는
    열화(2층)는 서로 다른 원인이라 어느 한쪽만으로 상대를 대체할 수 없다.

    caution/alarm이면 공통 출력 채널(server/dispatch/lcd_buzzer_output.py)로 알림을
    보낸다 — caution은 부저 없이 LCD 표시만, alarm은 부저+LCD 전부.
    """
    # 오프셋을 아무리 신중히 잡아도 노이즈로 raw가 오프셋보다 낮게 나올 수 있다.
    # 루프저항은 물리적으로 음수가 될 수 없으므로, 계산상 음수가 나오면 0으로 클램프한다
    # — 그렇지 않으면 대시보드에 "-3.8Ω" 같은 물리적으로 말이 안 되는 값이 노출된다.
    corrected = max(0, raw_resistance_ohm - LOOP_FIXED_OFFSET_OHM)

    status_1, reason_1 = check_absolute_threshold(corrected)
    slope_per_day, rttf_days, status_2, reason_2 = check_resistance_trend(conn, node_id, row_id)

    if SEVERITY_RANK[status_1] >= SEVERITY_RANK[status_2]:
        status, reason = status_1, reason_1
    else:
        status, reason = status_2, reason_2

    conn.execute(
        """UPDATE fire_alarm_log
           SET status=?, loop_trend_slope_ohm_per_day=?, loop_rttf_days=?
           WHERE id=?""",
        (status, slope_per_day, rttf_days, row_id),
    )
    conn.commit()

    if status in ("caution", "alarm"):
        trigger_alert(
            "fire_alarm", zone or node_id,
            reason or f"루프저항 이상 감지 (보정저항={corrected:.1f}Ω, 상태={status})",
            severity=status,
        )


# ---------------------------------------------------------------------------
# 가스계 / 유도등 선형회귀
# ---------------------------------------------------------------------------
def _fit_slope_intercept(ts_values, y_values):
    """(ts, y) 시계열에 1차 선형회귀 적용 -> (기울기, 절편) 반환. 데이터가 상수면 기울기 0."""
    ts_arr = np.array(ts_values, dtype=float)
    y_arr = np.array(y_values, dtype=float)
    # ts가 전부 동일하면(같은 타임스탬프로 몰림) polyfit이 특이행렬 오류를 낼 수 있어 방지
    if np.ptp(ts_arr) == 0:
        return 0.0, float(y_arr[-1])
    slope, intercept = np.polyfit(ts_arr, y_arr, 1)
    return float(slope), float(intercept)


def _agent_loss_pct(weight_g, initial_weight_g, empty_container_weight_g=0.0):
    """
    총중량이 아니라 약제중량(용기 자체 무게를 뺀 값) 기준으로 손실률을 계산한다.
    미니어처처럼 용기 무게 비중이 크면, 총중량 기준 계산은 실제 손실률을 과소평가하므로
    반드시 공병중량을 뺀 값으로 계산해야 한다(v7 버그수정, 파일 상단 설명 참고).
    """
    initial_agent = initial_weight_g - empty_container_weight_g
    current_agent = weight_g - empty_container_weight_g
    if initial_agent <= 0:
        return None  # 공병중량이 잘못 측정된 경우(총중량보다 크거나 같음) 방어
    return (initial_agent - current_agent) / initial_agent * 100.0


def evaluate_gas(conn, node_id: str, row_id: int, ts: float, weight_g: float,
                  initial_weight_g: float, gas_type: str = DEFAULT_GAS_TYPE, zone: str = None,
                  empty_container_weight_g: float = DEFAULT_EMPTY_CONTAINER_WEIGHT_G):
    """
    방금 적재된 gas_log 행에 대해 loss_pct(약제중량 기준 현재 손실률)와
    predicted_days_to_5pct(gas_type별 임계 손실률 도달 예상 일수)를 계산해 UPDATE한다.
    (필드명은 기존 스키마를 유지하되, 실제 임계값은 gas_type에 따라 다르게 적용한다)

    임계 손실률에 이미 도달했으면(predicted_days=0.0) 공통 출력 채널로 알림을 보낸다.
    """
    loss_pct = _agent_loss_pct(weight_g, initial_weight_g, empty_container_weight_g)
    target_pct = GAS_LOSS_THRESHOLD_PCT.get(gas_type, GAS_LOSS_THRESHOLD_PCT[DEFAULT_GAS_TYPE])

    cur = conn.execute(
        "SELECT ts, weight_g FROM gas_log WHERE node_id=? ORDER BY ts ASC",
        (node_id,),
    )
    rows = cur.fetchall()

    predicted_days = None
    if len(rows) >= MIN_POINTS:
        ts_values = [r[0] for r in rows]
        weight_values = [r[1] for r in rows]
        slope, intercept = _fit_slope_intercept(ts_values, weight_values)

        # 회귀는 실측되는 총중량(weight_g) 시계열로 하지만(공병중량은 시간에 따라 안 변하는
        # 상수라 기울기 자체는 총중량이든 약제중량이든 동일), 목표치 비교는 "약제중량 기준
        # 임계손실률"을 다시 총중량 스케일로 환산해서 비교해야 한다.
        initial_agent_weight = initial_weight_g - empty_container_weight_g
        target_agent_weight = initial_agent_weight * (1 - target_pct / 100.0)
        target_total_weight = target_agent_weight + empty_container_weight_g

        if slope < 0:
            # 이미 목표치를 넘어 손실된 경우 0으로 표시 (즉시 점검 필요)
            if weight_g <= target_total_weight:
                predicted_days = 0.0
            else:
                seconds_remaining = (target_total_weight - weight_g) / slope  # slope<0, 분자도 음수 -> 양수
                predicted_days = seconds_remaining / 86400.0
        # slope >= 0 (손실이 없거나 오히려 증가) -> 예측 불가, None 유지

    conn.execute(
        "UPDATE gas_log SET empty_container_weight_g=?, loss_pct=?, predicted_days_to_5pct=? WHERE id=?",
        (empty_container_weight_g, loss_pct, predicted_days, row_id),
    )
    conn.commit()

    if predicted_days is not None and predicted_days <= 0:
        trigger_alert(
            "gas", zone or node_id,
            f"{gas_type} 중량손실 임계치({target_pct}%) 도달 - 현재 손실률 {loss_pct:.1f}%",
            severity="alarm",
        )


def evaluate_evac_light(conn, node_id: str, row_id: int, ts: float, battery_voltage: float):
    """
    평상시(비-데모모드) 전압의 느린 달력 추세로 predicted_days_to_replace
    (배터리 노후화로 교체가 필요해질 것으로 예상되는 시점, 단위: 일)를 계산한다.

    이 함수는 "장기 열화 추세"만 다룬다 — 법정 20분/60분 기준과는 비교하지 않는다
    (그건 estimated_discharge_min의 역할, evaluate_evac_discharge_capacity 참고).
    데모모드 방전시험 구간의 급격한 전압 강하가 이 느린 추세 회귀에 섞이면 기울기가
    완전히 왜곡되므로, demo_mode=0인 평상시 데이터만 사용한다.
    """
    cur = conn.execute(
        "SELECT ts, battery_voltage FROM evac_light_log WHERE node_id=? AND demo_mode=0 ORDER BY ts ASC",
        (node_id,),
    )
    rows = cur.fetchall()

    predicted_days = None
    if len(rows) >= MIN_POINTS:
        ts_values = [r[0] for r in rows]
        voltage_values = [r[1] for r in rows]
        slope, intercept = _fit_slope_intercept(ts_values, voltage_values)

        if slope < 0:
            if battery_voltage <= EVAC_DISCHARGE_THRESHOLD_V:
                predicted_days = 0.0
            else:
                seconds_remaining = (EVAC_DISCHARGE_THRESHOLD_V - battery_voltage) / slope
                predicted_days = seconds_remaining / 86400.0
        # slope >= 0 (충전 중이거나 변화 없음) -> 예측 불가, None 유지

    conn.execute(
        "UPDATE evac_light_log SET predicted_days_to_replace=? WHERE id=?",
        (predicted_days, row_id),
    )
    conn.commit()


def _fetch_current_demo_session(conn, node_id: str, up_to_row_id: int):
    """
    up_to_row_id(포함)에서 거꾸로 훑어가며 demo_mode=1이 끊기지 않고 이어지는
    구간(=지금 진행 중인 방전시험 세션)만 뽑아 (ts, battery_voltage) 리스트로 반환한다
    (시간 오름차순). demo_mode=0을 만나면 그 이전은 이전 세션이므로 멈춘다.
    """
    rows = conn.execute(
        """SELECT ts, battery_voltage, demo_mode FROM evac_light_log
           WHERE node_id=? AND id<=? ORDER BY id DESC LIMIT 200""",
        (node_id, up_to_row_id),
    ).fetchall()

    session = []
    for ts_val, voltage_val, demo_mode in rows:
        if not demo_mode:
            break
        session.append((ts_val, voltage_val))
    session.reverse()
    return session


def evaluate_evac_discharge_capacity(conn, node_id: str, row_id: int, ts: float,
                                      battery_voltage: float,
                                      min_discharge_min: float = EVAC_MIN_DISCHARGE_MIN,
                                      zone: str = None):
    """
    데모모드(demo_mode=1) 방전시험 구간에서만 호출된다. 평상시 전압 추세(수백 일 단위
    느린 회귀)로는 실제 방전 지속시간을 알 수 없다 — 짧게라도 실제로 방전시켜봐야 안다.

    지금 진행 중인 방전시험 세션의 샘플들만 모아 짧은 구간 회귀로 "지금 이 순간
    정전이 나면 몇 분을 버티는지"(estimated_discharge_min)를 추정하고, 법정 최소
    작동시간(min_discharge_min, 기본 20분)에 못 미치면 status='warning'으로 UPDATE하고
    공통 출력 채널로 알림을 보낸다. 세션 샘플이 쌓일수록 매 패킷마다 추정치가
    갱신되어 시험이 끝날 때쯤 값이 수렴한다.
    """
    session = _fetch_current_demo_session(conn, node_id, row_id)
    if len(session) < MIN_DISCHARGE_TEST_POINTS:
        return  # 세션 초반 — 아직 기울기를 믿을 만큼 샘플이 안 모임, 에러 내지 않고 보류

    ts_values = [s[0] for s in session]
    voltage_values = [s[1] for s in session]
    slope, _ = _fit_slope_intercept(ts_values, voltage_values)

    if slope >= 0:
        return  # 방전 중인데 전압이 안 떨어짐 -> 측정 이상 의심, 추정 보류(에러 내지 않음)

    seconds_remaining = (battery_voltage - EVAC_DISCHARGE_THRESHOLD_V) / (-slope)
    estimated_min = max(seconds_remaining / 60.0, 0.0)
    status = "warning" if estimated_min < min_discharge_min else "normal"

    conn.execute(
        "UPDATE evac_light_log SET estimated_discharge_min=?, status=? WHERE id=?",
        (estimated_min, status, row_id),
    )
    conn.commit()

    if status == "warning":
        trigger_alert(
            "evac_light", zone or node_id,
            f"실측 방전지속시간 {estimated_min:.1f}분 - 법정 최소 {min_discharge_min:.0f}분 미달",
            severity="alarm",
        )
