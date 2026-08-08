"""
"추세 기반 통계 판정" 모듈 — 자탐 z-score + 가스계/유도등 선형회귀.
v7 폴더 재편으로 anomaly_detection.py와 regression_forecast.py를 이 파일 하나로
합쳤다 — 둘 다 "AI가 아니라 통계로 서서히 진행되는 추세를 잡아낸다"는 성격이 같기
때문. 함수명은 기존 그대로 유지했다(evaluate_fire_alarm/evaluate_gas/evaluate_evac_light/
evaluate_evac_discharge_capacity).

=== 자탐 z-score (evaluate_fire_alarm) ===
배경: 자탐 루프는 배선 열화(부식, 접촉불량)가 진행되면 저항이 서서히 상승한다.
법정 점검은 연 1~2회뿐이라 그 사이의 서서히 진행되는 열화를 못 잡는다.
그래서 상시 수신되는 저항값의 "최근 이동평균 대비 오늘 값이 얼마나 벗어났는가"를
z-score로 계산해 점검 사이 기간의 이상 징후를 잡아낸다.

방식: 새 패킷이 들어와 fire_alarm_log에 적재된 직후, 해당 node_id의 최근 이력을
가져와 (최신값을 제외한) 이동평균/이동표준편차를 baseline으로 삼고,
최신값이 그 baseline에서 몇 시그마 벗어났는지를 z_score로 기록한다.

판정 기준(v3 — 지속성/persistence 조건 추가):
  alarm:   최근 CONSECUTIVE_COUNT(기본 3)회 중 "연속으로" |z_score| > ALARM_THRESHOLD
  caution: 최근 CONSECUTIVE_COUNT회 중 CAUTION_MIN_HITS(기본 2)회 이상 |z_score| > CAUTION_THRESHOLD
  그 외          -> normal

v3에서 지속성 조건을 넣은 이유: 실제 실행 검증 결과 정상 구역에서도 순간적인 |z|>3가
우연히 한 번씩 튀는 경우(알람 1건, 주의 5건/100건, 약 5~6%)가 있었다. 통계적으로는
"드물게 우연히 있을 수 있는 정상 범위"이지만, 실전에서 관리자 입장에선 이 정도
오경보율도 시스템을 신뢰하지 못하게 만든다. 그런데 우리가 진짜로 잡으려는 건
"배선이 서서히 열화되는 추세"이지 "한 번 튄 순간값"이 아니다 — 그래서 순간적인
튐은 오히려 걸러내고, 최근 몇 회 연속/다수 초과할 때만 상태를 격상하는 게
애초 설계 의도(점진적 추세 탐지)에 더 맞다.

⚠️ 임계값(1.8/1.6)이 예전(3.0/2.0)보다 낮아진 이유 — 매직넘버가 아니라 수학적으로
유도된 값이다: 이동평균/이동표준편차 baseline은 "순수 선형 드리프트"에 대해서는
기울기(slope)가 아무리 커져도 z-score가 무한정 커지지 않고 창(window) 크기만으로
정해지는 점근값으로 수렴한다(창 안의 값들 자체도 같은 추세를 타고 있어서, 분자인
편차와 분모인 표준편차가 같이 커져 서로 상쇄되기 때문). 노이즈가 무시할 만큼 큰
드리프트에서 점근적으로 도달 가능한 최대 z는 sqrt(3)*sqrt((W+1)/(W-1))이고,
W=8이면 약 1.96이다. 즉 "|z|>3이 3회 연속"은 진짜 지속적인 열화로는 사실상 도달
불가능한 조건이고(순수 노이즈로 우연히 튀는 경우에만 가끔 나옴 — 지속성 조건과
정반대!), 그래서 실측 시뮬레이션으로 이 점근 한계보다 살짝 낮은 임계값을 찾아
"진짜 지속적 드리프트는 반드시 넘고, 노이즈만으로는 3회 연속 넘기 어려운" 지점으로
재보정했다. WINDOW_SIZE/임계값을 다시 조정할 때는 이 점근값 공식을 먼저 계산해서
알람 임계값이 그보다 낮은지 반드시 확인할 것.

주의: AI/ML이 아니라 순수 통계(이동평균/표준편차) 계산이다.
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
    ZSCORE_ALARM_THRESHOLD as ALARM_THRESHOLD,
    ZSCORE_CAUTION_MIN_HITS as CAUTION_MIN_HITS,
    ZSCORE_CAUTION_THRESHOLD as CAUTION_THRESHOLD,
    ZSCORE_CONSECUTIVE_COUNT as CONSECUTIVE_COUNT,
    ZSCORE_MIN_HISTORY as MIN_HISTORY,
    ZSCORE_WINDOW_SIZE as WINDOW_SIZE,
)
from dispatch.lcd_buzzer_output import trigger_alert  # noqa: E402

MIN_POINTS = 5                    # 회귀에 필요한 최소 데이터 개수 (미만이면 예측 보류)

EVAC_DISCHARGE_THRESHOLD_V = 3.0     # 유도등 배터리 방전 임계전압 (실측 후 재조정 가능)
EVAC_MIN_DISCHARGE_MIN = 20          # 법정 예비전원 최소 작동시간(일반) — §4
EVAC_MIN_DISCHARGE_MIN_EXTENDED = 60  # 11층 이상/지하 대형시설 등 — 참고용 옵션 상수
MIN_DISCHARGE_TEST_POINTS = 3         # 실측 방전시험 구간에서 기울기를 신뢰하기 위한 최소 샘플 수


# ---------------------------------------------------------------------------
# 자탐 z-score
# ---------------------------------------------------------------------------
def _fetch_history(conn, node_id: str, exclude_row_id: int, limit: int):
    """해당 노드의 최신 행을 제외한 과거 이력을 시간 오름차순으로 반환 (baseline 후보)"""
    cur = conn.execute(
        """SELECT loop_resistance_ohm FROM fire_alarm_log
           WHERE node_id = ? AND id != ? ORDER BY ts DESC, id DESC LIMIT ?""",
        (node_id, exclude_row_id, limit),
    )
    values = [v for (v,) in cur.fetchall()]
    values.reverse()
    return values


def _fetch_recent_z_scores(conn, node_id: str, exclude_row_id: int, limit: int):
    """이미 계산되어 저장된 최근 z_score들을 시간 오름차순으로 반환 (지속성 판단용)"""
    cur = conn.execute(
        """SELECT z_score FROM fire_alarm_log
           WHERE node_id = ? AND id != ? AND z_score IS NOT NULL
           ORDER BY ts DESC, id DESC LIMIT ?""",
        (node_id, exclude_row_id, limit),
    )
    values = [v for (v,) in cur.fetchall()]
    values.reverse()
    return values


def _decide_status(recent_z_scores_including_current):
    """
    최근 CONSECUTIVE_COUNT개의 z_score(가장 최근 것이 마지막 원소)를 보고 지속성
    조건에 따라 status를 정한다. 순간적인 튐이 아니라 반복적으로 벗어날 때만
    격상하는 게 설계 의도이므로, 표본이 CONSECUTIVE_COUNT개 다 모이기 전까지는
    (초기 구간) alarm으로 격상하지 않는다 — caution은 그보다는 덜 엄격하게,
    현재까지 모인 표본 안에서 CAUTION_MIN_HITS 이상이면 격상한다.
    """
    if len(recent_z_scores_including_current) >= CONSECUTIVE_COUNT:
        window = recent_z_scores_including_current[-CONSECUTIVE_COUNT:]
        if all(abs(z) > ALARM_THRESHOLD for z in window):
            return "alarm"
    else:
        window = recent_z_scores_including_current

    hits = sum(1 for z in window if abs(z) > CAUTION_THRESHOLD)
    if hits >= CAUTION_MIN_HITS:
        return "caution"
    return "normal"


def evaluate_fire_alarm(conn, node_id: str, row_id: int, latest_value: float, zone: str = None):
    """
    방금 적재된 fire_alarm_log 행(row_id, 값=latest_value)에 대해 z-score를 계산하고
    baseline_ohm / z_score / status를 UPDATE로 채워 넣는다.

    데이터가 부족하거나(MIN_HISTORY 미만) 표준편차가 0이면(값이 완전히 고정)
    z-score를 계산하지 않고 status='normal'을 유지한다 — 에러를 내지 않는 게 핵심.

    caution/alarm으로 판정되면 공통 출력 채널(server/dispatch/lcd_buzzer_output.py)로
    알림을 보낸다 — caution은 부저 없이 LCD 표시만(정상 구역에서도 노이즈로 종종 뜨는
    수준이라 부저까지 울리면 과민해 보임), alarm은 부저+LCD 전부.
    """
    hist_values = _fetch_history(conn, node_id, row_id, WINDOW_SIZE)

    if len(hist_values) < MIN_HISTORY:
        return  # 초기 구간 — baseline이 아직 불안정하므로 판정 보류

    n = len(hist_values)
    baseline = sum(hist_values) / n
    variance = sum((v - baseline) ** 2 for v in hist_values) / (n - 1) if n > 1 else 0.0
    std = variance ** 0.5

    if std == 0:
        # 이전 이력의 값이 전부 동일 — z-score 정의 불가, baseline만 기록
        conn.execute(
            "UPDATE fire_alarm_log SET baseline_ohm=?, z_score=NULL, status='normal' WHERE id=?",
            (baseline, row_id),
        )
        conn.commit()
        return

    z = (latest_value - baseline) / std

    recent_z_scores = _fetch_recent_z_scores(conn, node_id, row_id, CONSECUTIVE_COUNT - 1)
    status = _decide_status(recent_z_scores + [z])

    conn.execute(
        "UPDATE fire_alarm_log SET baseline_ohm=?, z_score=?, status=? WHERE id=?",
        (baseline, z, status, row_id),
    )
    conn.commit()

    if status in ("caution", "alarm"):
        trigger_alert(
            "fire_alarm", zone or node_id,
            f"루프 저항 z-score 이상 감지 (z={z:.2f}, 저항={latest_value:.2f}Ω, 상태={status})",
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
