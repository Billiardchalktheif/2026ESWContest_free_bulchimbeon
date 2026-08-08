"""
"규칙 기반 판정" 모듈 — 소화기 이탈감지 2단계 판정.
v7 폴더 재편으로 tamper_detection.py에서 이 위치(server/judge/rules.py)로 옮겨왔다.
로직/함수명은 그대로 유지했다(evaluate_tamper/resolve_stale_tamper_events).

설계 변경 배경(v1 -> v2): 압력게이지 판독 방식은 폐기했다. 게이지 지름이 30mm라
센서를 붙이기 물리적으로 어렵고, 카메라 방식은 복도 20m당 1개 x 층당 7개 규모로
배포하면 원가가 감당이 안 돼 "저비용 상시점검"이라는 프로젝트 취지 자체가 무너지기
때문이다. 대신 "소화기가 이동하면 가속도가 튄다"는 물리적 사실에 착안했다.

2단계로 나눈 이유: 가속도 센서만 쓰면 청소 중 접촉이나 점검자가 확인차 들었다 놓는
것도 전부 "이탈"로 오탐한다. 반대로 게이트웨이 연결상태만 보면 반응이 느리다(끊긴 걸
확인하려면 시간이 걸린다). 그래서 "가속도로 빠르게 1차 감지 -> 그 후 일정 시간 통신
유지 여부로 확정"하는 2단계 조합을 쓴다. (발표용 한 줄: "가속도센서는 빠르지만
오탐 가능성이 있고, 게이트웨이 연결상태는 느리지만 확실하다. 두 신호를 조합해
즉시성과 정확도를 동시에 확보했다.")

1단계 (accel_event=1, status='moved'): MPU6500 가속도 벡터 크기가 임계치를 넘으면
  즉시 1차 플래그만 세운다. 아직 경보 아님.
2단계 (CONFIRM_WINDOW_SEC 이내):
  - 그 후 도착하는 패킷이 "같은 게이트웨이"에서 왔으면 -> 오탐으로 판단, normal로 복귀
  - "다른 게이트웨이"에서 왔으면 -> 즉시 이탈 확정(missing)
  - 그 시간 안에 아무 패킷도 안 오면(완전 단절) -> resolve_stale_tamper_events()가
    주기적으로 스윕하며 이탈 확정 처리 (개별 패킷 처리로는 "안 옴"을 감지할 수 없어서
    서버 메인루프에서 시간 기반으로 별도 확인해야 함 — receiver/packet_parser.py의
    mark_offline_nodes()와 동일한 패턴)

단순화한 부분: "같은 게이트웨이 유지"는 확인 윈도 안에 도착한 패킷 1개만 봐도
같은 게이트웨이면 바로 오탐 판정한다(윈도 끝까지 안 기다림). 실무적으로 충분한
수준의 단순화이며, 팀 논의로 더 엄격하게 바꿀 수 있다.
"""
import sqlite3
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from dispatch.lcd_buzzer_output import trigger_alert  # noqa: E402

ACCEL_EVENT_THRESHOLD = 15.0   # m/s^2 — 정지 상태 중력가속도(~9.8)보다 충분히 큰 값, 실측 후 튜닝
CONFIRM_WINDOW_SEC = 60         # 오탐 필터 대기시간(§3)


def evaluate_tamper(conn: sqlite3.Connection, node_id: str, row_id: int,
                     accel_magnitude, gateway_id, ts: float, zone: str = None):
    """
    방금 적재된 extinguisher_log 행 하나를 보고 1단계(가속도 임계 초과) 판정과
    2단계(이전에 대기 중이던 이벤트의 오탐/이탈 확정)를 함께 처리한다.
    이탈이 확정(status='missing')되면 공통 출력 채널(server/dispatch/lcd_buzzer_output.py)로
    즉시 알림(부저+LCD)을 보낸다 — 도난 경보이므로 caution 단계 없이 바로 alarm.
    """
    accel_event = 1 if (accel_magnitude is not None and accel_magnitude > ACCEL_EVENT_THRESHOLD) else 0

    if accel_event:
        status = "moved"
    else:
        pending = conn.execute(
            """SELECT id, gateway_id FROM extinguisher_log
               WHERE node_id=? AND status='moved' AND id != ?
               ORDER BY id DESC LIMIT 1""",
            (node_id, row_id),
        ).fetchone()

        if pending is None:
            # 확인 대기 중인 이벤트가 없으면 직전 상태를 이어받는다 — 특히 직전이
            # 'missing'(이탈 확정)이었다면 계속 missing으로 유지해야 한다. 가속도가
            # 다시 잠잠해졌다고 자동으로 normal로 되돌리면 도난 경보가 조용히
            # 사라지는 심각한 오류가 된다. 실제 보안경보처럼 사람이 확인해서
            # 해제하기 전까지는 상태가 유지되는 게 맞다(수동 해제 기능은 이번 범위 밖).
            prev = conn.execute(
                "SELECT status FROM extinguisher_log WHERE node_id=? AND id != ? ORDER BY id DESC LIMIT 1",
                (node_id, row_id),
            ).fetchone()
            status = "missing" if (prev is not None and prev[0] == "missing") else "normal"
        else:
            pending_id, pending_gateway_id = pending
            if gateway_id is not None and gateway_id == pending_gateway_id:
                # 같은 게이트웨이로 계속 통신됨 -> 오탐, 대기 중이던 이벤트도 정상으로 되돌림
                conn.execute("UPDATE extinguisher_log SET status='normal' WHERE id=?", (pending_id,))
                status = "normal"
            else:
                # 다른 게이트웨이로 바뀜(또는 식별 불가) -> 이탈 확정
                conn.execute("UPDATE extinguisher_log SET status='missing' WHERE id=?", (pending_id,))
                status = "missing"

    conn.execute(
        "UPDATE extinguisher_log SET accel_event=?, status=? WHERE id=?",
        (accel_event, status, row_id),
    )
    conn.commit()

    if status == "missing":
        trigger_alert(
            "extinguisher", zone or node_id,
            f"소화기 이탈 확정 - 마지막 중계 게이트웨이: {gateway_id or 'N/A'}",
            severity="alarm",
        )


def resolve_stale_tamper_events(conn: sqlite3.Connection, now: float = None):
    """
    'moved' 상태로 CONFIRM_WINDOW_SEC 이상 방치된(=후속 패킷이 아예 안 온) 이벤트를
    '이탈 확정(missing)'으로 정리한다. 개별 패킷 처리만으로는 "패킷이 안 옴"을 감지할
    수 없어서, 서버 메인루프에서 주기적으로 호출해야 한다(receiver/udp_listener.py 참고).

    now 파라미터는 테스트에서 실제로 60초를 기다리지 않고 검증할 수 있도록 넣은
    것 — 운영 코드에서는 생략하면 실제 시각(time.time())을 쓴다.
    이 경로(완전 단절)로 이탈 확정되는 노드들도 evaluate_tamper()의 직접 확정 경로와
    동일하게 알림을 보내야 한다 — 그래서 UPDATE 전에 대상 행을 먼저 조회해 각각
    trigger_alert()를 호출한다.
    """
    if now is None:
        now = time.time()
    cutoff = now - CONFIRM_WINDOW_SEC

    stale_rows = conn.execute(
        "SELECT node_id, zone, gateway_id FROM extinguisher_log WHERE status='moved' AND ts < ?",
        (cutoff,),
    ).fetchall()

    conn.execute(
        "UPDATE extinguisher_log SET status='missing' WHERE status='moved' AND ts < ?",
        (cutoff,),
    )
    conn.commit()

    for node_id, zone, gateway_id in stale_rows:
        trigger_alert(
            "extinguisher", zone or node_id,
            f"소화기 이탈 확정 - 응답 없음(마지막 게이트웨이: {gateway_id or 'N/A'})",
            severity="alarm",
        )
