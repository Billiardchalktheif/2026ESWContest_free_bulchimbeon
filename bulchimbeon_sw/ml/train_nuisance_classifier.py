"""
자탐2(광전식/연기구역) 비화재보 판별 분류기 학습.
DB의 fire_alarm_log(zone_type='photoelectric', label IS NOT NULL) 데이터를 읽어
RandomForest로 fire(화재)/cooking(조리 수증기)/normal(평상시) 3클래스 분류 모델을 학습한다.

ml/train_pump_classifier.py와 동일한 구조(학습/평가/저장 방식)를 그대로 재사용했다 —
수계 파형 분류와 마찬가지로 "여러 값을 동시에 봐야 패턴이 구분되는" 문제라 같은
접근(RandomForest, classical ML)이 적합하다(설계원칙 §2-1).

⚠️ 학습 데이터 재현이 이 프로젝트에서 가장 불확실한 부분이다(§4). fire/cooking/normal
세 상태가 실측에서도 실제로 잘 구분되는지(온도상승률 vs 습도상승 조합)는 부품 수령 후
가장 먼저 검증해야 한다. 만약 신호가 겹치면 이 스크립트의 혼동행렬(confusion matrix)에
바로 드러나므로, 학습 직후 반드시 확인할 것 — 겹치는 게 확인되면 규칙 기반(예: 습도
임계값만으로 단순 필터링)으로 축소하는 대안도 준비돼 있다(§7).

사용법: python ml/train_nuisance_classifier.py
결과: server/judge/ai_models/nuisance_classifier.joblib 저장 + 정확도/혼동행렬/feature importance 출력
"""
import sqlite3
import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

sys.path.append(str(Path(__file__).parent.parent / "server"))
from config import AI_MODELS_DIR, DB_PATH  # noqa: E402

MODEL_PATH = AI_MODELS_DIR / "nuisance_classifier.joblib"

FEATURE_COLS = ["temp_rise_rate", "mq2_raw", "humidity_pct"]


def load_labeled_data() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """SELECT temp_rise_rate, mq2_raw, humidity_pct, label FROM fire_alarm_log
           WHERE zone_type='photoelectric' AND label IS NOT NULL""",
        conn,
    )
    conn.close()
    return df


def train():
    df = load_labeled_data()
    if len(df) < 20:
        print(f"[경고] 학습 데이터가 {len(df)}개뿐입니다. 최소 30개 이상 권장.")
        if len(df) == 0:
            return

    X = df[FEATURE_COLS]
    y = df["label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )

    clf = RandomForestClassifier(n_estimators=200, max_depth=6, random_state=42)
    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_test)

    print("=" * 50)
    print(f"학습 데이터: {len(X_train)}개 / 테스트 데이터: {len(X_test)}개")
    print("=" * 50)
    print("\n[분류 리포트]")
    print(classification_report(y_test, y_pred))

    print("[혼동 행렬] (행=실제, 열=예측) - fire/cooking 신호가 겹치면 여기 바로 드러남")
    labels = sorted(y.unique())
    cm = confusion_matrix(y_test, y_pred, labels=labels)
    print("     ", "  ".join(f"{l:>9}" for l in labels))
    for label, row in zip(labels, cm):
        print(f"{label:>9}", "  ".join(f"{v:>9}" for v in row))

    print("\n[Feature importance] - 왜 이렇게 판정했는지 설명하는 근거")
    importances = sorted(zip(FEATURE_COLS, clf.feature_importances_), key=lambda x: -x[1])
    for name, imp in importances:
        bar = "#" * int(imp * 40)
        print(f"  {name:<14} {imp:.3f} {bar}")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)  # ai_models/ 디렉터리가 없으면 생성
    joblib.dump(clf, MODEL_PATH)
    print(f"\n모델 저장 완료: {MODEL_PATH}")


if __name__ == "__main__":
    train()
