"""
AI 모델 로드 + 실시간 추론 모듈 — 수계 주펌프 파형 분류 + 자탐2 비화재보 판별.
v7 폴더 재편으로 ml/predict_pump.py와 nuisance_alarm_classifier.py를 이 파일 하나로
합쳤다. 이 프로젝트에서 AI/ML은 정확히 이 두 지점에만 적용한다 — "여러 값을 동시에
보고 패턴을 구분해야 하는" 문제라는 공통점이 있어 예외적으로 AI가 필요하다고 팀이
확정한 곳들이다. 그 외 설비는 전부 통계/규칙 기반(server/judge/regression.py,
server/judge/rules.py 참고).

두 분류기 모두 같은 패턴을 따른다: 학습 스크립트(ml/train_*.py)가 저장한 모델을
최초 1회만 로드해 캐싱하고, 모델 파일이 없으면(=아직 학습 전) 예외를 던지지 않고
조용히 (None, None)을 반환한다 — 서버가 죽으면 안 되기 때문이다.

=== 수계 주펌프 파형 분류 (predict_pump_label) ===
train_pump_classifier.py가 학습해 저장한 모델(pump_classifier.joblib)을 불러와,
label 없이 들어온(=학습용이 아니라 실측 운영) 파형 feature에 대해 추론 결과를
predicted_label/confidence로 돌려준다.

=== 자탐2 비화재보 판별 (predict_nuisance_label / handle_nuisance_prediction) ===
연기감지기(광전식)는 담배연기·조리 수증기에도 오작동하는 경우가 많다.
온도상승률(temp_rise_rate)·가스농도(mq2_raw)·습도(humidity_pct) 세 값을 동시에
봐야 fire(진짜 화재)/cooking(조리 수증기)/normal(평상시)이 구분되는 패턴 문제라서
단순 if문 조합으로는 깔끔하게 안 나뉜다.
predict_nuisance_label()은 추론만 하는 순수 함수로 유지하고(conn/DB 접근 없음),
"fire로 판정되면 알림을 보낸다"는 알림 로직은 handle_nuisance_prediction()에
분리해뒀다 — receiver/packet_parser.py가 추론 직후 이 함수를 호출한다.
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from config import AI_MODELS_DIR  # noqa: E402
from dispatch.lcd_buzzer_output import trigger_alert  # noqa: E402

# ---------------------------------------------------------------------------
# 수계 주펌프 파형 분류
# ---------------------------------------------------------------------------
PUMP_MODEL_PATH = AI_MODELS_DIR / "pump_classifier.joblib"
PUMP_FEATURE_COLS = ["rms", "peak", "duty_cycle"]

_pump_model = None
_pump_model_load_attempted = False


def _get_pump_model():
    """모델을 최초 1회만 로드해 캐싱한다 (매 패킷마다 디스크 I/O 하지 않기 위함)"""
    global _pump_model, _pump_model_load_attempted
    if _pump_model_load_attempted:
        return _pump_model
    _pump_model_load_attempted = True
    if not PUMP_MODEL_PATH.exists():
        return None
    try:
        import joblib
        _pump_model = joblib.load(PUMP_MODEL_PATH)
    except Exception as e:
        print(f"[WARN] 수계 분류 모델 로드 실패: {e}")
        _pump_model = None
    return _pump_model


def predict_pump_label(rms: float, peak: float, duty_cycle: float):
    """
    (rms, peak, duty_cycle) feature로 5클래스(normal_operation/stall_operation/dry_run/
    flow_reduced/startup_failure) 중 추론. 반환: (predicted_label, confidence) 또는
    모델이 없으면 (None, None)
    """
    model = _get_pump_model()
    if model is None:
        return None, None
    try:
        import pandas as pd
        X = pd.DataFrame([[rms, peak, duty_cycle]], columns=PUMP_FEATURE_COLS)
        proba = model.predict_proba(X)[0]
        classes = model.classes_
        best_idx = proba.argmax()
        return str(classes[best_idx]), float(proba[best_idx])
    except Exception as e:
        print(f"[WARN] 수계 분류 추론 실패: {e}")
        return None, None


# ---------------------------------------------------------------------------
# 자탐2(광전식/연기구역) 비화재보 판별
# ---------------------------------------------------------------------------
NUISANCE_MODEL_PATH = AI_MODELS_DIR / "nuisance_classifier.joblib"
NUISANCE_FEATURE_COLS = ["temp_rise_rate", "mq2_raw", "humidity_pct", "mq2_to_humidity_ratio"]

_nuisance_model = None
_nuisance_model_load_attempted = False


def _get_nuisance_model():
    """모델을 최초 1회만 로드해 캐싱한다 (매 패킷마다 디스크 I/O 하지 않기 위함)"""
    global _nuisance_model, _nuisance_model_load_attempted
    if _nuisance_model_load_attempted:
        return _nuisance_model
    _nuisance_model_load_attempted = True
    if not NUISANCE_MODEL_PATH.exists():
        return None
    try:
        import joblib
        _nuisance_model = joblib.load(NUISANCE_MODEL_PATH)
    except Exception as e:
        print(f"[WARN] 비화재보 판별 모델 로드 실패: {e}")
        _nuisance_model = None
    return _nuisance_model


def predict_nuisance_label(temp_rise_rate: float, mq2_raw: float, humidity_pct: float,
                            mq2_to_humidity_ratio: float):
    """
    (temp_rise_rate, mq2_raw, humidity_pct, mq2_to_humidity_ratio) feature로
    normal/smoking/incense/heat/fire 추론 (5클래스, ml/train_nuisance_classifier.py와 동일).
    넷 중 하나라도 없으면 추론하지 않는다.
    반환: (predicted_label, confidence) 또는 모델이 없거나 입력 부족하면 (None, None)
    """
    if None in (temp_rise_rate, mq2_raw, humidity_pct, mq2_to_humidity_ratio):
        return None, None
    model = _get_nuisance_model()
    if model is None:
        return None, None
    try:
        import pandas as pd
        X = pd.DataFrame(
            [[temp_rise_rate, mq2_raw, humidity_pct, mq2_to_humidity_ratio]],
            columns=NUISANCE_FEATURE_COLS,
        )
        proba = model.predict_proba(X)[0]
        classes = model.classes_
        best_idx = proba.argmax()
        return str(classes[best_idx]), float(proba[best_idx])
    except Exception as e:
        print(f"[WARN] 비화재보 판별 추론 실패: {e}")
        return None, None


def handle_nuisance_prediction(zone_or_node_id: str, predicted_label, confidence):
    """
    predict_nuisance_label()의 추론 결과를 보고 fire로 판정됐을 때만 공통 출력
    채널로 알림을 보낸다. cooking/normal은 알림 대상이 아니다(비화재보란 원래
    "화재 아닌데 화재로 오인하는 것을 걸러내는" 목적이므로, fire가 아닌 판정은
    조용히 넘어가는 게 이 기능의 존재 이유에 맞다).
    """
    if predicted_label != "fire":
        return
    trigger_alert(
        "fire_alarm", zone_or_node_id,
        f"비화재보 판별 AI: 화재로 판정됨 (신뢰도 {confidence:.0%})" if confidence is not None
        else "비화재보 판별 AI: 화재로 판정됨",
        severity="alarm",
    )
