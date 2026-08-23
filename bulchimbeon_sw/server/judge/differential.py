"""
자탐1(차동식구역) 온도 상승률 판정 — 감지기의 형식승인 및 검정기술기준 제14조
(1종: 10℃/min 이상=화재로 판정, 2℃/min 이하는 15분 지속돼도 오작동 금지) 적용.

루프저항 판정(judge/regression.py의 evaluate_loop_resistance)과는 완전히 별개다 —
같은 fire_alarm_log 행에 있지만 저장 컬럼이 다르다(temp_status vs status). 절대
같은 컬럼을 공유하지 않는다.

⚠️ 전제조건(2026-08-23 기준): fire_alarm_differential_node.ino가 아직
temp_raw_adc를 보내지 않는다 — 펌웨어 반영 전까지 이 함수는 호출되긴 하지만
temp_raw_adc가 없으므로 즉시 판정을 보류(normal)하고 조용히 리턴한다
(receiver/packet_parser.py의 None 가드 참고). temp_c/temp_rise_rate(ESP32 계산값)는
캘리브레이션 미확정 placeholder라 판정에 쓰지 않는다 — 반드시 temp_raw_adc 기준.

점검모드: 이 함수는 항상 상시로 판정만 계산한다. 그 결과를 진짜 경보로 낼지
시험 기록으로만 남길지는 test_mode.handle_judgment()가 결정한다(§7-1 원칙 —
판정 로직 자체는 점검모드와 무관하게 절대 안 바뀐다).
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from test_mode import handle_judgment  # noqa: E402

# 캘리브레이션 회귀식 — 잠정치(인수인계 문서 §4), 작동시험 데이터로 최종 확정 예정.
# raw는 온도가 오르면 감소한다(TEMP_RAW_PER_C가 음수인 회귀식 기준) — 그래서
# "하락폭"으로 화재를 판단한다.
RATE_WINDOW_SAMPLES = 5              # 25초 창(5초 전송 간격 기준) — 5초 단일 샘플로 판정 금지
ALARM_RAW_DROP_PER_WINDOW = 500      # 잠정치 — 10℃/min 이상 상승에 해당하는 raw 하락폭


def _fetch_recent_raw_samples(conn, node_id: str, up_to_row_id: int):
    """현재 행 포함, 최근 RATE_WINDOW_SAMPLES개를 (ts, temp_raw_adc) 시간 오름차순으로 반환.
    temp_raw_adc가 NULL인 행(구형 펌웨어 패킷)은 애초에 제외한다."""
    rows = conn.execute(
        """SELECT ts, temp_raw_adc FROM fire_alarm_log
           WHERE node_id = ? AND id <= ? AND temp_raw_adc IS NOT NULL
           ORDER BY id DESC LIMIT ?""",
        (node_id, up_to_row_id, RATE_WINDOW_SAMPLES),
    ).fetchall()
    rows.reverse()
    return rows


def evaluate_temp_rise_rate(conn, node_id: str, row_id: int, temp_raw_adc,
                             zone: str = None, packet_test_mode: bool = False):
    """
    자탐1 온도 상승률 최종 판정. packet_parser.py가 fire_alarm_log INSERT 직후,
    zone_type == 'differential'이고 temp_raw_adc가 있을 때만 호출한다.

    temp_raw_adc가 None이면(펌웨어 미반영 상태) 판정을 건너뛰고 조용히 리턴한다 —
    이 상태에서 억지로 판정하면 항상 normal로 채워져 "판정이 동작하는 것처럼"
    보이는 게 오히려 위험하므로, temp_status를 아예 NULL로 남겨 "아직 판정 안 됨"과
    "판정했더니 정상"을 구분한다.
    """
    if temp_raw_adc is None:
        return

    samples = _fetch_recent_raw_samples(conn, node_id, row_id)
    if len(samples) < RATE_WINDOW_SAMPLES:
        status = "normal"  # 초기 구간 — 표본 부족, 오경보 방지 위해 normal 유지
    else:
        raw_drop = samples[0][1] - samples[-1][1]
        status = "alarm" if raw_drop >= ALARM_RAW_DROP_PER_WINDOW else "normal"

    conn.execute("UPDATE fire_alarm_log SET temp_status=? WHERE id=?", (status, row_id))
    conn.commit()

    handle_judgment(
        conn, device="fire_alarm", zone=zone or node_id, node_id=node_id,
        status=status, packet_test_mode=packet_test_mode,
        message=f"자탐1 온도상승률 이상 감지 (raw 하락폭 임계 {ALARM_RAW_DROP_PER_WINDOW} 이상)",
    )
