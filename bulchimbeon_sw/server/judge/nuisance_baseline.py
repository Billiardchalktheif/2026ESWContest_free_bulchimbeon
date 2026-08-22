"""
자탐2(광전식) 비화재보 판별의 실시간 기준점(baseline) 관리.

학습 데이터(photoelectric_all_classes_combined.csv)는 "세션 시작 대비 상승률/변화폭"을
feature로 쓰지만, 실시간 운영 중엔 "세션 시작"이라는 개념이 없다 — 24시간 상시감시라
사건이 언제 시작되는지 아무도 알려주지 않는다. 그래서 이중모드로 기준점을 만든다:

- 방법 A(기본, 항상 동작): 최근 5분 이동평균을 기준점으로 자동 계산
- 방법 B(시연용, 선택): 대시보드 버튼으로 트리거하면 그 순간을 3분간 고정 기준점으로 사용

우선순위는 server/pump_performance_test.py의 determine_valve_state()와 동일한 패턴
— 방법 B(수동 트리거)가 신선하면 우선, 없으면 방법 A(자동)로 폴백.

ESP32는 원시 순간값(mq2_raw, humidity_pct, temp_c)만 보낸다 — 기준점/상승률 계산은
전부 여기서 서버가 담당한다.
"""
import json
import time
from collections import deque
from pathlib import Path

ROLLING_WINDOW_SEC = 5 * 60       # 방법 A: 5분 전 상태를 기준점으로 삼음
BASELINE_TOLERANCE_SEC = 30       # 5분 전 근방 ±30초 내 표본이 있어야 기준점으로 인정
MAX_HISTORY_SEC = 20 * 60         # 메모리에 유지할 최대 이력 길이 (5분 기준 + 여유)

DEMO_TRIGGER_FRESHNESS_SEC = 3 * 60  # 방법 B: 트리거 후 3분간 유지, 지나면 자동으로 방법 A 복귀
DEMO_TRIGGER_PATH = Path(__file__).parent.parent / "storage" / "_nuisance_demo_trigger.json"

# node_id -> deque[(ts, mq2_raw, humidity_pct, temp_c)] — 방법 A용 최근 이력 (인메모리)
_history: dict = {}


def record_reading(node_id: str, ts: float, mq2_raw: float, humidity_pct: float, temp_c: float):
    """매 패킷마다 원시값을 이력에 기록. 오래된 건 자동으로 정리한다."""
    buf = _history.setdefault(node_id, deque())
    buf.append((ts, mq2_raw, humidity_pct, temp_c))
    cutoff = ts - MAX_HISTORY_SEC
    while buf and buf[0][0] < cutoff:
        buf.popleft()


def _moving_baseline(node_id: str, now_ts: float):
    """방법 A: 정확히 5분 전 근방(±30초)의 평균을 기준점으로 삼는다.
    아직 5분치 이력이 없으면(운영 시작 직후) None — 이 경우 판정을 보류한다."""
    buf = _history.get(node_id)
    if not buf:
        return None
    target_ts = now_ts - ROLLING_WINDOW_SEC
    candidates = [r for r in buf if abs(r[0] - target_ts) <= BASELINE_TOLERANCE_SEC]
    if not candidates:
        return None
    n = len(candidates)
    return {
        "mq2_raw": sum(r[1] for r in candidates) / n,
        "humidity_pct": sum(r[2] for r in candidates) / n,
        "temp_c": sum(r[3] for r in candidates) / n,
        "source": "moving_avg",
    }


def request_demo_trigger(node_id: str):
    """방법 B 시작 요청 — 대시보드(Flask) 프로세스에서 호출된다. 이 프로세스는
    receiver의 _history를 갖고 있지 않으므로, 원시값 스냅샷 없이 "언제 눌렸는지"
    시각만 파일에 남긴다. 실제 기준점 계산은 receiver 프로세스의 _demo_baseline()이
    자신의 _history로 직접 한다 — 데이터를 가진 프로세스가 계산까지 담당한다는
    수계 트리거(determine_valve_state)와 동일한 원칙.

    펌프 트리거와 달리 여기는 3분 내내 같은 기준점을 유지해야 하므로, 소비 후
    파일을 지우지 않는다 — 유효기간(DEMO_TRIGGER_FRESHNESS_SEC)이 지나면 자연히
    무효화된다.
    """
    DEMO_TRIGGER_PATH.write_text(
        json.dumps({"node_id": node_id, "trigger_ts": time.time()}),
        encoding="utf-8",
    )


def _demo_baseline(node_id: str, now_ts: float):
    """방법 B: receiver 프로세스에서만 호출됨. 트리거 파일이 신선하면(3분 이내),
    트리거 시각 근방(±30초)의 자기 _history 표본으로 고정 기준점을 계산한다."""
    if not DEMO_TRIGGER_PATH.exists():
        return None
    try:
        data = json.loads(DEMO_TRIGGER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("node_id") != node_id:
        return None
    trigger_ts = data.get("trigger_ts", 0)
    if now_ts - trigger_ts > DEMO_TRIGGER_FRESHNESS_SEC:
        return None  # 유효기간 지남 — 자동으로 방법 A로 복귀

    buf = _history.get(node_id)
    if not buf:
        return None
    candidates = [r for r in buf if abs(r[0] - trigger_ts) <= BASELINE_TOLERANCE_SEC]
    if not candidates:
        return None
    n = len(candidates)
    return {
        "mq2_raw": sum(r[1] for r in candidates) / n,
        "humidity_pct": sum(r[2] for r in candidates) / n,
        "temp_c": sum(r[3] for r in candidates) / n,
        "source": "demo_trigger",
    }


def get_active_baseline(node_id: str, now_ts: float = None):
    """기준점 결정 — 방법 B(신선하면)가 방법 A보다 우선."""
    now_ts = now_ts or time.time()
    demo = _demo_baseline(node_id, now_ts)
    if demo is not None:
        return demo
    return _moving_baseline(node_id, now_ts)


def compute_nuisance_features(node_id: str, mq2_raw, humidity_pct, temp_c, ts: float = None):
    """
    원시값을 기록하고, 활성 기준점 대비 학습 때와 동일한 4개 feature를 계산해 반환.
    - temp_rise_rate: 기준점 대비 온도 변화(°C) — 컬럼명은 유지하되 의미는 "상승폭"
    - mq2_raw: 기준점 대비 MQ2 상승률(%)
    - humidity_pct: 기준점 대비 습도 변화(%p)
    - mq2_to_humidity_ratio: 위 둘의 비율 (train_nuisance_classifier.py와 동일 공식)
    입력값이 없거나 기준점이 아직 준비 안 됐으면(운영 시작 직후 5분 이내) None 반환.
    """
    if mq2_raw is None or humidity_pct is None or temp_c is None:
        return None
    ts = ts or time.time()
    record_reading(node_id, ts, mq2_raw, humidity_pct, temp_c)

    baseline = get_active_baseline(node_id, ts)
    if baseline is None:
        return None

    temp_rise_rate = temp_c - baseline["temp_c"]
    base_mq2 = baseline["mq2_raw"]
    mq2_feature = ((mq2_raw - base_mq2) / base_mq2 * 100.0) if base_mq2 else 0.0
    humidity_feature = humidity_pct - baseline["humidity_pct"]
    ratio = mq2_feature / (abs(humidity_feature) + 1.0)

    return {
        "temp_rise_rate": temp_rise_rate,
        "mq2_raw": mq2_feature,
        "humidity_pct": humidity_feature,
        "mq2_to_humidity_ratio": ratio,
        "baseline_source": baseline["source"],
    }
