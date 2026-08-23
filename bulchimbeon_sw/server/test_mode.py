"""
점검모드(Test Mode) 공통 유틸리티 — 자탐1(차동식) 최초 적용, 추후 자탐2/소화기 확장
대상(인수인계 문서 §7-4). 판정 로직(evaluate_*())은 점검모드와 무관하게 항상 그대로
계산되고, 이 모듈은 그 결과를 "진짜 경보로 내보낼지" vs "시험 기록으로만 로그에
남길지"만 결정한다 — 판정 로직 자체를 이 모듈이 건드리는 일은 절대 없다.

상태 저장 불필요: 노드가 이미 5초마다 보내는 UDP 패킷에 test_mode 필드가 실려오므로
서버는 그 값을 그대로 매 패킷마다 넘겨받아 분기만 하면 된다(별도 start/end API 없음).
"""
import time

from dispatch.lcd_buzzer_output import trigger_alert


def log_test_event(conn, device_type: str, zone: str, node_id: str,
                    judged_status: str, ts: float = None):
    """점검모드 중 판정 결과를 test_mode_log에 기록만 한다 — 합격/불합격 자동판정
    없음, 사람이 나중에 로그를 보고 직접 해석한다(인수인계 문서 §7-1)."""
    conn.execute(
        """INSERT INTO test_mode_log (ts, device_type, zone, node_id, judged_status)
           VALUES (?,?,?,?,?)""",
        (ts if ts is not None else time.time(), device_type, zone, node_id, judged_status),
    )
    conn.commit()


def handle_judgment(conn, device: str, zone: str, node_id: str, status: str,
                     packet_test_mode: bool, message: str = None):
    """
    evaluate_*() 결과와 패킷의 test_mode 값을 넘기면 알아서 분기 처리.
      packet_test_mode=True  -> 시험 기록으로만 로그 남김 (진짜 경보 안 나감)
      packet_test_mode=False -> 평소와 동일 — status가 caution/alarm이면 실제 경보
    """
    if packet_test_mode:
        log_test_event(conn, device, zone, node_id, status)
        return
    if status in ("caution", "alarm"):
        trigger_alert(device, zone, message or f"{device} 이상 감지 (상태={status})", severity=status)
