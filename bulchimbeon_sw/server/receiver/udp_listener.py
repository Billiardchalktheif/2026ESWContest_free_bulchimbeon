"""
UDP 소켓 수신 루프.
v7 폴더 재편으로 udp_receiver.py의 run_server() 안 while문을 이 위치
(server/receiver/udp_listener.py)로 분리했다. 패킷 1개를 어떻게 해석/적재/판정할지는
packet_parser.py가 맡고, 이 파일은 "소켓을 열고 계속 받아서 넘겨준다"는 통신 계층
역할만 한다 — 판정 로직과 소켓 I/O를 분리해두면, 나중에 전송방식이 바뀌어도(예:
TCP, MQTT) 판정 로직 파일은 안 건드려도 된다.

run_server()는 server/main.py가 그대로 호출하는 진입점이다.
"""
import json
import socket
import sqlite3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from config import DB_PATH  # noqa: E402
from judge.rules import resolve_stale_tamper_events  # noqa: E402
from receiver.packet_parser import handle_packet, mark_offline_nodes  # noqa: E402

UDP_IP = "0.0.0.0"
UDP_PORT = 9000


def run_server():
    conn = sqlite3.connect(DB_PATH)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)  # 1MB로 확장, 순간 몰림 대비
    sock.bind((UDP_IP, UDP_PORT))
    sock.settimeout(5.0)
    print(f"UDP 수신 대기 중: {UDP_IP}:{UDP_PORT}")

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            pkt = json.loads(data.decode("utf-8"))
            handle_packet(conn, pkt)
        except socket.timeout:
            mark_offline_nodes(conn)
            resolve_stale_tamper_events(conn)  # 후속 패킷이 아예 안 온 이탈 이벤트를 시간 기반으로 확정
        except json.JSONDecodeError:
            print(f"[WARN] JSON 파싱 실패: {data}")
        except KeyError as e:
            print(f"[WARN] 필수 필드 누락: {e}")
        except Exception as e:
            # 알 수 없는 오류는 서버 전체를 죽이지 않고 로그만 남긴다.
            import traceback
            print(f"[ERROR] 패킷 처리 중 예외 발생: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    run_server()
