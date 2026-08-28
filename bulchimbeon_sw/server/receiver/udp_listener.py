"""
UDP 소켓 수신 루프.
v7 폴더 재편으로 udp_receiver.py의 run_server() 안 while문을 이 위치
(server/receiver/udp_listener.py)로 분리했다. 패킷 1개를 어떻게 해석/적재/판정할지는
packet_parser.py가 맡고, 이 파일은 "소켓을 열고 계속 받아서 넘겨준다"는 통신 계층
역할만 한다 — 판정 로직과 소켓 I/O를 분리해두면, 나중에 전송방식이 바뀌어도(예:
TCP, MQTT) 판정 로직 파일은 안 건드려도 된다.

run_server()는 server/main.py가 그대로 호출하는 진입점이다.

⚠️ 버그수정 (2026-08-27): mark_offline_nodes()/resolve_stale_tamper_events()를
socket.timeout 예외 안에서만 호출했더니, 여러 노드가 같은 UDP 포트를 공유하는
구조상 다른 노드 트래픽이 계속 있으면 소켓이 5초간 쉬는 일이 사실상 없어서 이
두 함수가 거의 호출되지 않는 문제가 있었다(소화기 이탈 "확정"이 실제로 통신이
끊겨도 절대 안 뜨는 버그로 나타남 — 1차 감지는 되는데 시간기반 확정 스윕
자체가 실행이 안 됐던 것). 그래서 패킷 수신 여부와 무관하게, 매 루프 반복마다
경과시간을 확인해서 SWEEP_INTERVAL_SEC마다 무조건 실행하도록 바꿨다.
"""
import json
import socket
import sqlite3
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from config import DB_PATH  # noqa: E402
from judge.rules import resolve_stale_tamper_events  # noqa: E402
from receiver.packet_parser import handle_packet, mark_offline_nodes  # noqa: E402

UDP_IP = "0.0.0.0"
UDP_PORT = 9000
SWEEP_INTERVAL_SEC = 5  # 오프라인 판정/이탈 확정 스윕 최소 주기 — 다른 노드 트래픽으로
                         # socket.timeout이 안 걸려도 이 주기마다는 무조건 스윕한다


def run_server():
    conn = sqlite3.connect(DB_PATH)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)  # 1MB로 확장, 순간 몰림 대비
    sock.bind((UDP_IP, UDP_PORT))
    sock.settimeout(5.0)
    print(f"UDP 수신 대기 중: {UDP_IP}:{UDP_PORT}")

    last_sweep = time.time()

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            pkt = json.loads(data.decode("utf-8"))
            handle_packet(conn, pkt)
        except socket.timeout:
            pass
        except json.JSONDecodeError:
            print(f"[WARN] JSON 파싱 실패: {data}")
        except KeyError as e:
            print(f"[WARN] 필수 필드 누락: {e}")
        except Exception as e:
            # 알 수 없는 오류는 서버 전체를 죽이지 않고 로그만 남긴다.
            import traceback
            print(f"[ERROR] 패킷 처리 중 예외 발생: {e}")
            traceback.print_exc()

        # 패킷을 받았든 못 받았든(다른 노드 트래픽으로 timeout이 안 걸렸든) 상관없이
        # 이 주기마다는 무조건 스윕한다 — 위 버그수정 설명 참고.
        now = time.time()
        if now - last_sweep >= SWEEP_INTERVAL_SEC:
            mark_offline_nodes(conn)
            resolve_stale_tamper_events(conn)  # 후속 패킷이 아예 안 온 이탈 이벤트를 시간 기반으로 확정
            last_sweep = now


if __name__ == "__main__":
    run_server()
