"""
불침번 서버 진입점.
v7 폴더 재편으로 receiver/storage/judge/dispatch 서브패키지로 나뉜 뒤, 실행은
이 파일 하나로 통일한다: python server/main.py

DB가 아직 없으면(최초 실행) 먼저 server/storage/db.py로 초기화할 것:
    python server/storage/db.py
"""
from receiver.udp_listener import run_server

if __name__ == "__main__":
    run_server()
