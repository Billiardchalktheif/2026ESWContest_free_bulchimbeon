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
    "co2": 10.0,     # ⚠️ 소방시설 점검관리 매뉴얼 1권 10%(예외 있음) vs 2권 5% — 팀 확인 대기 중,
                     # 확정 전까지 보수적으로 낮은 값(5%) 채택
    "halon": 5.0,   # 압력손실 10% 미만 '또는' 중량손실 5% 미만 -> 중량 기준(5%) 적용
    "inert": 5.0,   # 원 기준은 압력손실 5% 미만(IG계열) -> 중량센서로 근사 적용
}
# ⚠️ 공병(약제 없는 용기) 중량 — 반드시 설치 현장에서 실측 캘리브레이션 필요.
# ESP32는 이 값을 보내지 않으므로 서버가 여기서 가져와 evaluate_gas() 호출 시 넘겨준다.
# 0.0으로 두면 총중량 기준 계산이 되어 손실률이 과소평가된다(v7 버그수정 배경 참고).
DEFAULT_EMPTY_CONTAINER_WEIGHT_G = 260.0

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
# 하루당 저항 상승폭(Ω/day) 기준. 법정기준(LOOP_RESISTANCE_HARD_LIMIT_OHM)과 달리
# 이 값은 팀 자체 설계값이며, 실측 노이즈 통계 기반으로 재조정됨.
#
# ⚠️ 아래는 "결선(실사용) 전용" 값이다(2026-08-16 2차 재조정) — 시연 영상(3분)에는
# 자탐 회귀분석(2층 추세 판정) 자체를 노출하지 않기로 결정됐으므로, 이번 재조정은
# "3분 내 반응"이라는 제약을 완전히 버리고 노이즈 억제(오탐 최소화)만 최우선으로
# 다시 탐색한 결과다. 대신 결선 후 실제 감지까지 시간이 이전보다 훨씬 길어졌다
# (최초 판정까지 LOOP_TREND_MIN_SAMPLES*LOOP_TREND_DOWNSAMPLE_EVERY_N*5초 =
# 15*100*5 = 7500초 ≈ 2.08시간, 회귀 창 전체가 다 채워져 가장 안정될 때까지는
# LOOP_TREND_HISTORY_LIMIT*LOOP_TREND_DOWNSAMPLE_EVERY_N*5초 = 50*100*5 = 25000초
# ≈ 6.94시간) — 실제 부식은 원래 수주~수개월 단위로 진행되는 현상이라(설계 문서
# 기준) 이 정도 지연은 결선 목적에 문제되지 않는다는 판단.
#
# 1차 재조정(2026-08-16, 시연 3분 제약이 있던 시절 — 지금은 폐기됨, 참고용 기록):
#   LOOP_TREND_DOWNSAMPLE_EVERY_N=5, LOOP_TREND_HISTORY_LIMIT=20 기준으로
#   ALARM_SLOPE=700 / CAUTION_SLOPE=230으로 튜닝했었다. D시나리오 노이즈 표준편차가
#   당시 수백 Ω/day대였기 때문 — 지금의 훨씬 넓은 시간창 기준으로는 이 값들이 더는
#   유효하지 않다(시간창이 바뀌면 노이즈 분포 자체가 달라짐).
#
# 2차 재조정(현재값 확정 근거): LOOP_TREND_DOWNSAMPLE_EVERY_N=100/
# LOOP_TREND_HISTORY_LIMIT=50(회귀.py) 기준으로 D시나리오(자탐1 실물과 동일한 5초
# 간격 순수 노이즈, FIRE_ALARM_NOISE_STD=0.3Ω)를 10회 반복 시뮬레이션(각 15000
# 패킷 = 약 20.8시간)해 steady-state 기울기 표준편차 ~0.45Ω/day, 99th percentile
# ~0.92Ω/day를 실측했다. 이 분포를 넉넉히 벗어나는 값으로 아래를 확정한 결과
# D시나리오 alarm 오탐 비율 0%(10회 반복 전부)를 달성했다. 동시에 "며칠~2주"
# 단위로 50Ω에 도달하는 진짜 열화(예: 7일 내 도달하는 가속 프로파일)는 caution이
# 약 2.3일째, alarm이 약 6.4일째에 안정적으로(순서대로) 잡히는 것도 함께 확인했다
# (simulate/verify_loop_resistance_judgment.py B시나리오 참고).
LOOP_TREND_ALARM_SLOPE = 8
LOOP_TREND_CAUTION_SLOPE = 3
LOOP_TREND_MIN_SAMPLES = 15  # 이 미만이면 추세 판정 보류(NORMAL 유지). 결선 전용 재조정으로
                              # 3 -> 15로 상향(초기 워밍업 구간의 불안정한 기울기를 더 넓게
                              # 걸러내기 위함) — 반응속도보다 정확도를 우선한 선택

# 시연 모드 — True면 대시보드가 자탐 2층(추세) 판정 필드(열화 추세, 잔여 고장시간
# 예측)를 숨긴다. LOOP_TREND_ALARM_SLOPE/CAUTION_SLOPE(2026-08-16 결선 전용 재조정,
# 위 참고)는 반응속도보다 정확도를 우선한 값이라, 시연용 짧은 스트림에서는 "표본부족"
# 문구만 계속 뜨거나(더미데이터) 실물 하드웨어 기준으로는 결선용 숫자가 그대로
# 노출되어(예: MIN_SAMPLES=15는 5초 간격 기준 75초면 충족됨) 시연 취지와 안 맞는다.
# 결선 때는 반드시 False로 바꿀 것 — 이 판정이 이 프로젝트의 핵심 기능 중 하나이므로
# 결선에서는 숨기지 않고 그대로 노출해야 한다.
DEMO_MODE = True

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
