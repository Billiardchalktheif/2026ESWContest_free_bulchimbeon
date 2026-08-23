"""
수계 충압펌프(jockey) 파형 분류기 학습.
주펌프는 전류센서(INA219)가 없어 파형 데이터 자체가 없다 — AI 분류 대상에서 제외.
DB의 water_pump_log(pump_type='jockey', label IS NOT NULL) 데이터를 읽어
RandomForest로 normal/low_flow/dryrun/start_fail 4클래스 분류 모델을 학습한다.
(체절 클래스는 포함하지 않음 — 충압펌프는 배관이 거의 충수된 상태에서 짧게
보충하는 역할이라 체절과 정상운전의 파형 차이가 뚜렷하지 않을 것으로 판단해
데이터 채집 단계에서 제외됨. data/수계 엣지/충압펌프_전류파형_분석_보고서.md 참고)

⚠️ normal과 low_flow의 RMS 값이 83.7% 겹친다(분석 보고서 §4.2) — RMS 단일
지표로는 완전 분리가 안 되고 Peak/Duty까지 같이 봐야 한다. 또한 dryrun(39개)이
low_flow(867개) 대비 극히 적어(1/22 수준) class_weight='balanced'로 보정한다.

사용법: python ml/train_pump_classifier.py
결과: server/judge/ai_models/pump_classifier.joblib 저장 + 정확도/혼동행렬/feature importance 출력
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

MODEL_PATH = AI_MODELS_DIR / "pump_classifier.joblib"

FEATURE_COLS = ["rms", "peak", "duty_cycle"]
MIN_PERFORMANCE_TEST_SAMPLES = 30  # 이 개수 이상이면 성능시험 라벨만으로 학습(신뢰도 우선)


def load_labeled_data() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    performance_df = pd.read_sql_query(
        """SELECT rms, peak, duty_cycle, label FROM water_pump_log
           WHERE pump_type='jockey' AND label IS NOT NULL AND label_source='performance_test'""",
        conn,
    )
    if len(performance_df) >= MIN_PERFORMANCE_TEST_SAMPLES:
        conn.close()
        print(f"[정보] 성능시험 라벨 {len(performance_df)}개로 학습 (신뢰도 우선, manual 라벨 미포함)")
        return performance_df

    # 성능시험 라벨이 아직 부족한 초기 단계 — manual 라벨도 보조로 포함해 학습 자체는 가능하게 함
    manual_df = pd.read_sql_query(
        """SELECT rms, peak, duty_cycle, label FROM water_pump_log
           WHERE pump_type='jockey' AND label IS NOT NULL
             AND (label_source IS NULL OR label_source='manual')""",
        conn,
    )
    conn.close()
    print(
        f"[정보] 성능시험 라벨이 {len(performance_df)}개뿐이라 "
        f"manual 라벨 {len(manual_df)}개를 보조로 포함해 학습"
    )
    return pd.concat([performance_df, manual_df], ignore_index=True)


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

    clf = RandomForestClassifier(
        n_estimators=200, max_depth=6, random_state=42, class_weight="balanced"
    )
    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_test)

    print("=" * 50)
    print(f"학습 데이터: {len(X_train)}개 / 테스트 데이터: {len(X_test)}개")
    print("=" * 50)
    print("\n[분류 리포트]")
    print(classification_report(y_test, y_pred))

    print("[혼동 행렬] (행=실제, 열=예측)")
    labels = sorted(y.unique())
    cm = confusion_matrix(y_test, y_pred, labels=labels)
    print("     ", "  ".join(f"{l:>9}" for l in labels))
    for label, row in zip(labels, cm):
        print(f"{label:>9}", "  ".join(f"{v:>9}" for v in row))

    print("\n[Feature importance] - 왜 이렇게 판정했는지 설명하는 근거")
    importances = sorted(zip(FEATURE_COLS, clf.feature_importances_), key=lambda x: -x[1])
    for name, imp in importances:
        bar = "#" * int(imp * 40)
        print(f"  {name:<12} {imp:.3f} {bar}")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)  # ai_models/ 디렉터리가 없으면 생성
    joblib.dump(clf, MODEL_PATH)
    print(f"\n모델 저장 완료: {MODEL_PATH}")


if __name__ == "__main__":
    train()
