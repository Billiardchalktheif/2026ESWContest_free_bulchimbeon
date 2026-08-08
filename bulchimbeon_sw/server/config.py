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

# ---- 자탐 z-score 지속성(persistence) 조건 (server/judge/regression.py에서 사용) ----
ZSCORE_WINDOW_SIZE = 8        # baseline 계산에 사용할 최근 이력 개수
# 주의: window가 너무 크면 이동평균 자체가 서서히 진행되는 드리프트를 따라가버려서
# (baseline이 최신값을 뒤쫓듯 같이 상승) z-score가 항상 낮게 나와 알람이 안 뜨는
# 문제가 생긴다. 실측 시뮬레이션으로 튜닝한 결과 8 부근이 균형점이었다.
ZSCORE_MIN_HISTORY = 5        # 이 개수 미만이면 baseline이 불안정하므로 z-score 계산 보류
# 임계값(1.8/1.6)이 통상적인 3.0/2.0보다 낮은 이유는 매직넘버가 아니라 수학적으로 유도된
# 값이다 — 자세한 유도 과정은 server/judge/regression.py의 evaluate_fire_alarm docstring 참고.
ZSCORE_CAUTION_THRESHOLD = 1.6
ZSCORE_ALARM_THRESHOLD = 1.8
ZSCORE_CONSECUTIVE_COUNT = 3     # "최근 몇 회"를 지속성 판단 창으로 볼지
ZSCORE_CAUTION_MIN_HITS = 2      # caution: 이 창 안에서 몇 회 이상 초과해야 격상할지(다수결, 연속 아님)
