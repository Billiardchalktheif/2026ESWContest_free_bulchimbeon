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
from sklearn.model_selection import StratifiedKFold, cross_val_predict

sys.path.append(str(Path(__file__).parent.parent / "server"))
from config import AI_MODELS_DIR, DB_PATH  # noqa: E402

MODEL_PATH = AI_MODELS_DIR / "nuisance_classifier.joblib"

FEATURE_COLS = ["temp_rise_rate", "mq2_raw", "humidity_pct", "mq2_to_humidity_ratio"]


def load_labeled_data() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """SELECT temp_rise_rate, mq2_raw, humidity_pct, label FROM fire_alarm_log
           WHERE zone_type='photoelectric' AND label IS NOT NULL""",
        conn,
    )
    conn.close()
    # incense는 "MQ2만 크게 뛰고 습도는 거의 안 움직인다"는 시그니처가 있고,
    # smoking(연초/전자담배)은 MQ2 상승과 함께 습도도 같이 오른다 — 담배 연기에
    # 수증기가 섞여 나오기 때문. 단일 feature로는 안 갈리던 이 경계가 "MQ2 상승
    # 대비 습도 상승 비율"로는 갈릴 가능성이 있어 파생 feature로 추가한다.
    # humidity_pct가 0에 가까운 세션(특히 incense)에서 0으로 나누는 걸 막기 위해
    # +1.0을 더한다(epsilon). abs()는 heat/fire처럼 습도가 크게 "하강"하는
    # 경우에도 비율이 항상 양수로 나오게 하기 위함.
    df["mq2_to_humidity_ratio"] = df["mq2_raw"] / (df["humidity_pct"].abs() + 1.0)
    return df


def train():
    df = load_labeled_data()
    if len(df) < 20:
        print(f"[경고] 학습 데이터가 {len(df)}개뿐입니다. 최소 30개 이상 권장.")
        if len(df) == 0:
            return

    X = df[FEATURE_COLS]
    y = df["label"]

    # --- 5-fold 교차검증: 모든 표본이 최소 한 번은 테스트로 쓰이도록 함 ---
    # 표본이 적은 클래스(예: fire 9개)에서도 n_splits=5가 성립하는지 자동 확인.
    min_class_count = y.value_counts().min()
    n_splits = min(5, min_class_count)
    if n_splits < 2:
        print(f"[경고] 가장 적은 클래스 표본이 {min_class_count}개뿐이라 교차검증 생략")
    else:
        skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        cv_clf = RandomForestClassifier(n_estimators=200, max_depth=6, random_state=42)
        oof_pred = cross_val_predict(cv_clf, X, y, cv=skf)

        print("=" * 50)
        print(f"[교차검증 {n_splits}-fold] 전체 {len(y)}개 표본이 전부 한 번씩 테스트됨")
        print("=" * 50)
        print("\n[교차검증 분류 리포트]")
        print(classification_report(y, oof_pred))

        print("[교차검증 혼동 행렬] (행=실제, 열=예측)")
        cv_labels = sorted(y.unique())
        cv_cm = confusion_matrix(y, oof_pred, labels=cv_labels)
        print("     ", "  ".join(f"{l:>9}" for l in cv_labels))
        for label, row in zip(cv_labels, cv_cm):
            print(f"{label:>9}", "  ".join(f"{v:>9}" for v in row))
        print()

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
