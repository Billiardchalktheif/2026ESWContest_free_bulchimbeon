"""
불침번 — 소화기 게이트웨이 USB Serial -> UDP 브릿지 (pyserial 버전)
"""

import json
import socket
import time

import serial

SERIAL_PORT = "/dev/ttyUSB0"
BAUD_RATE = 115200
UDP_TARGET_IP = "127.0.0.1"
UDP_TARGET_PORT = 9000
RECONNECT_DELAY_SEC = 3


def open_serial():
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            print(f"[연결됨] {SERIAL_PORT} @ {BAUD_RATE}bps")
            return ser
        except serial.SerialException as e:
            print(f"[대기] 시리얼 포트 연결 실패 ({e}) — {RECONNECT_DELAY_SEC}초 후 재시도")
            time.sleep(RECONNECT_DELAY_SEC)


def main():
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ser = open_serial()

    print(f"[시작] 시리얼->UDP 브릿지 실행 중 (-> {UDP_TARGET_IP}:{UDP_TARGET_PORT})")
    print("종료하려면 Ctrl+C")

    while True:
        try:
            raw_line = ser.readline()
            if not raw_line:
                continue

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

        except serial.SerialException:
            print("[끊김] 시리얼 연결 끊김 — 재연결 시도")
            try:
                ser.close()
            except Exception:
                pass
            ser = open_serial()
        except KeyboardInterrupt:
            print("\n[종료] 브릿지 중단")
            break


if __name__ == "__main__":
    main()
