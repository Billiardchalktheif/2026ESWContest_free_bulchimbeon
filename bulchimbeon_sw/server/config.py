"""
불침번 서버 설정값 모음 — v7 폴더 재편으로 여기저기 흩어져 있던 상수들을 모았다.

경로 상수(DB_PATH 등)는 재편으로 파일 위치가 바뀌면서 각 모듈이 저마다
`Path(__file__).parent.parent / "db" / ...` 식으로 상대경로를 계산하던 걸 한 곳으로
모은 것 — 폴더 구조가 또 바뀌어도 여기 한 곳만 고치면 되게 하기 위함이다.

나머지 상수(가스계 임계값, z-score 지속성 조건 등)는 여러 판정 모듈에 걸쳐 쓰이거나
설치 현장마다 실측 후 조정해야 하는 값들이라 여기 모아뒀다. 모듈 하나에서만 쓰는
상수(예: 소화기 가속도 임계값, 성능시험 압력 임계값)는 해당 모듈에 그대로 남겨뒀다 —
전부 여기로 옮기면 오히려 "이 상수가 어디서 쓰이는지" 추적하기 어려워지기 때문이다.
"""
from pathlib import Path

# ---- 경로 ----
SERVER_DIR = Path(__file__).parent
PROJECT_ROOT = SERVER_DIR.parent
DB_PATH = SERVER_DIR / "storage" / "bulchimbeon.db"
SCHEMA_PATH = SERVER_DIR / "storage" / "schema.sql"
AI_MODELS_DIR = SERVER_DIR / "judge" / "ai_models"

# ---- 가스계 (server/judge/regression.py에서 사용) ----
DEFAULT_GAS_TYPE = "co2"
GAS_LOSS_THRESHOLD_PCT = {
    "co2": 5.0,     # ⚠️ 소방시설 점검관리 매뉴얼 1권 10%(예외 있음) vs 2권 5% — 팀 확인 대기 중,
                     # 확정 전까지 보수적으로 낮은 값(5%) 채택
    "halon": 5.0,   # 압력손실 10% 미만 '또는' 중량손실 5% 미만 -> 중량 기준(5%) 적용
    "inert": 5.0,   # 원 기준은 압력손실 5% 미만(IG계열) -> 중량센서로 근사 적용
}
# ⚠️ 공병(약제 없는 용기) 중량 — 반드시 설치 현장에서 실측 캘리브레이션 필요.
# ESP32는 이 값을 보내지 않으므로 서버가 여기서 가져와 evaluate_gas() 호출 시 넘겨준다.
# 0.0으로 두면 총중량 기준 계산이 되어 손실률이 과소평가된다(v7 버그수정 배경 참고).
DEFAULT_EMPTY_CONTAINER_WEIGHT_G = 0.0

# ---- 자탐 루프저항 판정 (server/judge/regression.py의 evaluate_loop_resistance에서 사용) ----
# 두 구역(자탐1 차동식/자탐2 광전식) 공통 — 루프저항 회로가 동일 설계이기 때문.
# z-score(이동평균 기반) 판정은 폐기했다 — baseline 자체가 서서히 상승하는 저항값을
# 따라가버려서, 진짜 "가속 열화"를 정상 범위로 오판하는 문제가 실측 검증 중 발견됐다.
# 급성 단선/합선은 실제 화재수신기가 도통시험으로 이미 실시간 감시 중이므로, 상위
# SW는 법정기준(50Ω) 초과 여부(절대 임계값, 1층)와 그 이전 서서히 진행되는 열화
# 추세(선형회귀 기울기, 2층)를 보는 2층 구조로 재정립했다.
LOOP_RESISTANCE_HARD_LIMIT_OHM = 50.0  # 법정기준 (소방시설등 점검관리 매뉴얼 - 회로저항시험)
# ⚠️ 회로 스케일 보정.
# 정상 상태 raw 값은 보통 233~236Ω 사이에서 흔들리지만, 물리적 접촉·진동 등으로
# 순간적으로 230Ω까지 떨어지는 경우가 실측으로 확인됐다(2026-08-16). 루프저항은
# "열화 없음" 상태가 최솟값이라는 물리적 의미상, 오프셋은 평균값이 아니라 관측된
# 최솟값 근처로 잡아야 한다 — 평균값으로 잡으면 정상적인 노이즈 하락만으로도
# corrected 값이 음수가 되는 문제가 발생하기 때문.
LOOP_FIXED_OFFSET_OHM = 230
# 하루당 저항 상승폭(Ω/day) 기준 — 임시값, 실측 데이터 축적 후 재조정 필요
LOOP_TREND_ALARM_SLOPE = 2.0
LOOP_TREND_CAUTION_SLOPE = 0.5
LOOP_TREND_MIN_SAMPLES = 3  # 이 미만이면 추세 판정 보류(NORMAL 유지) — 가스계/유도등의
                             # MIN_POINTS(5)와 별개 상수. 자탐은 초기 반응성을 더 우선시함

# ---- online/offline 판정 기준 (device_type별, receiver/packet_parser.py의
# mark_offline_nodes / dashboard/app.py의 get_heartbeats에서 사용) ----
# 장비마다 정상적인 전송 주기가 크게 달라서(자탐/수계/소화기/유도등은 초 단위,
# 가스계는 실배포 기준 1일 간격) 공통 타임아웃 하나로는 가스계가 항상 offline로
# 오판된다 — 하루에 한 번만 보내는데 90초 무수신 기준을 적용하면 항상 timeout이기
# 때문. device_type 기준으로 분리했다.
HEARTBEAT_TIMEOUT_SEC = {
    "fire_alarm": 90,
    "water_pump": 90,
    "extinguisher": 90,
    "evac_light": 90,
    "gas": 130000,  # 가스 ESP32의 실배포 기준 SEND_INTERVAL_MS(86400000ms=1일)의 1.5배 여유.
                     # ⚠️ 가스 노드를 시연용으로 짧은 주기(예: 5초)로 바꿔놔도 이 값은 실배포
                     # 기준 그대로 둘 것 — 시연 중엔 90초보다 훨씬 자주 오므로 여유 있는
                     # 130000초 기준을 그대로 써도 online으로 정상 표시된다(esp32/gas_node/
                     # gas_node.ino 상단 주석 참고).
}
DEFAULT_HEARTBEAT_TIMEOUT_SEC = 90  # device_type이 위 딕셔너리에 없을 때 fallback


def is_node_online(device_type: str, last_seen: float, now: float) -> bool:
    """device_type별 heartbeat 타임아웃 기준으로 온라인 여부를 판정하는 공통 진입점.
    packet_parser.py(오프라인 표시)와 dashboard/app.py(배지 표시)가 똑같이 이 함수만
    거치도록 해서, 두 곳의 판정 기준이 따로 놀지 않게 한다."""
    timeout = HEARTBEAT_TIMEOUT_SEC.get(device_type, DEFAULT_HEARTBEAT_TIMEOUT_SEC)
    return (now - last_seen) <= timeout
