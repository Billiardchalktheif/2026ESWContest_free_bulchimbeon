"""
불침번 — 소화기 게이트웨이 USB Serial -> UDP 브릿지 (pyserial 불필요 버전)
"""

import json
import os
import socket
import termios
import time

SERIAL_PORT = "/dev/ttyUSB0"
UDP_TARGET_IP = "127.0.0.1"
UDP_TARGET_PORT = 9000
RECONNECT_DELAY_SEC = 3


def open_serial_raw(port_path, baud=termios.B115200):
    fd = os.open(port_path, os.O_RDWR | os.O_NOCTTY)
    attrs = termios.tcgetattr(fd)
    attrs[4] = baud
    attrs[5] = baud
    attrs[3] = attrs[3] & ~(termios.ICANON | termios.ECHO | termios.ECHOE | termios.ISIG)
    attrs[0] = attrs[0] & ~(termios.IXON | termios.IXOFF | termios.IXANY)
    attrs[2] = (attrs[2] & ~termios.CSIZE) | termios.CS8
    attrs[2] = attrs[2] & ~termios.PARENB
    attrs[2] = attrs[2] & ~termios.CSTOPB
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def open_serial_with_retry():
    while True:
        try:
            fd = open_serial_raw(SERIAL_PORT)
            print(f"[연결됨] {SERIAL_PORT}")
            return fd
        except (FileNotFoundError, PermissionError, OSError) as e:
            print(f"[대기] 포트 열기 실패 ({e}) — {RECONNECT_DELAY_SEC}초 후 재시도")
            time.sleep(RECONNECT_DELAY_SEC)


def read_line(fd, buf):
    while b"\n" not in buf:
        chunk = os.read(fd, 256)
        if not chunk:
            raise OSError("포트에서 읽기 실패 (연결 끊김 추정)")
        buf += chunk
    line, _, rest = buf.partition(b"\n")
    return line, rest


def main():
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fd = open_serial_with_retry()
    buf = b""

    print(f"[시작] 시리얼->UDP 브릿지 실행 중 (-> {UDP_TARGET_IP}:{UDP_TARGET_PORT})")
    print("종료하려면 Ctrl+C")

    while True:
        try:
            raw_line, buf = read_line(fd, buf)
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            if not line.startswith("{"):
                print(f"[디버그] {line}")
                continue

            try:
                packet = json.loads(line)
            except json.JSONDecodeError:
                print(f"[WARN] JSON 파싱 실패, 건너뜀: {line}")
                continue

            packet["ts"] = time.time()

            udp_sock.sendto(json.dumps(packet).encode("utf-8"),
                             (UDP_TARGET_IP, UDP_TARGET_PORT))
            print(f"[릴레이] {packet.get('node_id', '?')} seq={packet.get('seq', '?')}")

        except OSError:
            print("[끊김] 시리얼 연결 끊김 — 재연결 시도")
            try:
                os.close(fd)
            except Exception:
                pass
            fd = open_serial_with_retry()
            buf = b""
        except KeyboardInterrupt:
            print("\n[종료] 브릿지 중단")
            break


if __name__ == "__main__":
    main()
