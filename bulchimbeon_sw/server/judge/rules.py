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

⚠️ v2 -> v3 로직 변경 (소화기 리프노드 딥슬립 제거에 맞춘 재설계):
기존 v2는 "1단계(moved) 이후 도착하는 바로 다음 패킷 1개"만 보고 즉시 오탐/이탈을
판정했다. 이건 리프노드가 딥슬립+인터럽트 웨이크업이라 패킷이 드문드문(웨이크업할
때만) 오는 걸 전제로 한 설계였다 — 패킷 간격이 넓으니 "다음 패킷 = 시간이 꽤 지난
뒤의 패킷"이라는 암묵적 가정이 성립했었다.

근데 리프노드가 딥슬립을 버리고 200ms 폴링 + 자주 전송하는 구조로 바뀌면서 이
가정이 깨졌다. 이제 "다음 패킷"은 보통 1~2초 안에 도착하는 정상 수치라서, 어쩌다
한 번 가속도가 튀어도(moved) 그 즉시 다음 패킷에 자동으로 오탐 취소(normal)돼버려
"확인 시간(CONFIRM_WINDOW_SEC)"이라는 값 자체가 사실상 아무 의미가 없어지는
문제가 있었다(실측 중 발견 — 노드를 흔들고 신호 안 잡히는 곳으로 이동시켜도
이탈 확정이 전혀 안 뜨는 현상으로 나타남).

v3는 판정을 시간 기준으로 완전히 재설계했다:
  1단계 (accel_event=1, status='moved'): 가속도 임계 초과 시 즉시 1차 플래그.
    이 시점에는 오탐/이탈 어느 쪽으로도 확정하지 않는다 — evaluate_tamper()는
    이제 이 판단을 전혀 안 한다.
  2단계 (resolve_stale_tamper_events()가 주기적으로 스윕): 'moved'로 찍힌 지
    CONFIRM_WINDOW_SEC 이상 지난 이벤트를 찾아서,
      - 그 이후로도 그 노드의 패킷이 (내용과 무관하게) 계속 들어왔다면
        -> 오탐, normal로 복귀
      - 그 이후로 그 노드의 패킷이 완전히 끊겼다면 -> 이탈 확정(missing)
  즉 "1차 이벤트 감지 -> 일정 시간 뒤에 실제로 계속 살아있었는지를 보고 확정"
  하는 순수 시간 기반 판정이 됐다. "바로 다음 패킷 하나만 보고 즉시 판정"하던
  기존 로직의 운빨 요소가 사라졌다.
"""
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from dispatch.lcd_buzzer_output import trigger_alert  # noqa: E402

ACCEL_EVENT_THRESHOLD = 15.0   # m/s^2 — 정지 상태 중력가속도(~9.8)보다 충분히 큰 값, 실측 후 튜닝
CONFIRM_WINDOW_SEC = 10         # 오탐 필터 대기시간(§3) — 시연 중엔 8~10 정도로 낮춰서 테스트 가능

# ---------------------------------------------------------------------------
# 소화기 내용연수 판정 — 이탈감지(위 evaluate_tamper)와는 완전히 별개 축이다.
# 이탈감지는 "지금 그 자리에 있는가"를 보고, 이 함수는 "그 소화기 자체가 아직
# 쓸 수 있는 연식인가"를 본다 — 센서 없이 날짜 계산만으로 되는 저비용 확장.
# ---------------------------------------------------------------------------
EXTINGUISHER_WARNING_WINDOW_DAYS = 180  # 만료 6개월 전부터 "교체 임박" — 팀 조정 가능한 placeholder


def check_extinguisher_lifespan(expiry_date_str):
    """
    소화기 내용연수 판정 — 만료일자를 직접 입력받는다(제조일자+10년 역산 방식 폐기).
    이유: 2017년 개정 소방시설법 이후 소화기 라벨에 내용연수 만료일자가 직접 인쇄되어
    있는 경우가 많고, 성능확인검사 통과 시 10년에서 3년 연장되는 예외가 있어 제조일자
    기준 고정 계산(+10년)으로는 이런 케이스를 반영할 수 없다. 라벨 값을 그대로 입력받는
    쪽이 더 정확하다.

    expiry_date_str: "YYYY-MM-DD" 형식 (소화기 라벨의 내용연수 만료일자). None/빈값이면 미입력.
    반환값: (상태문자열, 남은일수)
      상태: "미입력" / "정상" / "교체 임박" / "만료"
      남은일수: 미입력이면 None, 그 외엔 int (음수면 만료 후 경과일수)
    """
    if not expiry_date_str:
        return "미입력", None

    expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d")
    days_remaining = (expiry_date - datetime.now()).days

    if days_remaining <= 0:
        return "만료", days_remaining
    elif days_remaining <= EXTINGUISHER_WARNING_WINDOW_DAYS:
        return "교체 임박", days_remaining
    else:
        return "정상", days_remaining


def evaluate_tamper(conn: sqlite3.Connection, node_id: str, row_id: int,
                     accel_magnitude, gateway_id, ts: float, zone: str = None):
    """
    방금 적재된 extinguisher_log 행 하나에 대해 1단계(가속도 임계 초과) 플래그만
    세운다. v3부터는 오탐/이탈 "확정"을 여기서 하지 않는다 — 그건 순수 시간
    기반으로 resolve_stale_tamper_events()가 주기적으로 처리한다(아래 참고).
    이 함수는 매 패킷마다 호출되므로 가벼운 작업만 한다.
    """
    accel_event = 1 if (accel_magnitude is not None and accel_magnitude > ACCEL_EVENT_THRESHOLD) else 0

    if accel_event:
        status = "moved"
    else:
        # 확인 대기 중인 이전 상태를 이어받는다 — 특히 직전이 'missing'(이탈 확정)
        # 이었다면 계속 missing으로 유지해야 한다. 가속도가 다시 잠잠해졌다고
        # 자동으로 normal로 되돌리면 도난 경보가 조용히 사라지는 심각한 오류가
        # 된다. 실제 보안경보처럼 사람이 확인해서 해제하기 전까지는 상태가
        # 유지되는 게 맞다(수동 해제 기능은 이번 범위 밖).
        prev = conn.execute(
            "SELECT status FROM extinguisher_log WHERE node_id=? AND id != ? ORDER BY id DESC LIMIT 1",
            (node_id, row_id),
        ).fetchone()
        status = "missing" if (prev is not None and prev[0] == "missing") else "normal"

    conn.execute(
        "UPDATE extinguisher_log SET accel_event=?, status=? WHERE id=?",
        (accel_event, status, row_id),
    )
    conn.commit()
    # 'missing' 확정 알림은 여기서 안 보낸다 — resolve_stale_tamper_events()가
    # 시간이 지난 뒤 실제로 확정될 때 한 번만 보낸다.


def resolve_stale_tamper_events(conn: sqlite3.Connection, now: float = None):
    """
    'moved' 상태로 찍힌 지 CONFIRM_WINDOW_SEC 이상 지난 이벤트를 찾아서,
    그 이후로 해당 노드의 패킷이 (내용과 무관하게) 계속 들어왔는지를 보고
    오탐(normal)/이탈확정(missing)을 최종 판정한다. 서버 메인루프에서
    주기적으로 호출해야 한다(receiver/udp_listener.py 참고).

    now 파라미터는 테스트에서 실제로 60초를 기다리지 않고 검증할 수 있도록 넣은
    것 — 운영 코드에서는 생략하면 실제 시각(time.time())을 쓴다.
    """
    if now is None:
        now = time.time()
    cutoff = now - CONFIRM_WINDOW_SEC

    moved_rows = conn.execute(
        "SELECT id, node_id, zone, gateway_id, ts FROM extinguisher_log WHERE status='moved' AND ts < ?",
        (cutoff,),
    ).fetchall()

    for row_id, node_id, zone, gateway_id, moved_ts in moved_rows:
        # 이 moved 이벤트 이후에 이 노드의 패킷이 (accel_event 여부와 무관하게)
        # 하나라도 더 들어왔는지 확인 — 들어왔다면 "그동안 계속 살아있었다"는
        # 뜻이니 오탐으로 판단한다.
        later = conn.execute(
            "SELECT id FROM extinguisher_log WHERE node_id=? AND ts > ? LIMIT 1",
            (node_id, moved_ts),
        ).fetchone()

        if later is not None:
            conn.execute("UPDATE extinguisher_log SET status='normal' WHERE id=?", (row_id,))
            conn.commit()
        else:
            conn.execute("UPDATE extinguisher_log SET status='missing' WHERE id=?", (row_id,))
            conn.commit()
            trigger_alert(
                "extinguisher", zone or node_id,
                f"소화기 이탈 확정 - 응답 없음(마지막 게이트웨이: {gateway_id or 'N/A'})",
                severity="alarm",
            )
