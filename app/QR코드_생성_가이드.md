# QR코드 생성 가이드 — A방식 (짧은 코드 방식)

> QR코드 안에는 짧은 식별자(예: `EXT-01`)만 넣고, 우리 앱이 스캔 후 자체 서버(`/api/equipment/<코드>`)에 물어보는 방식. 이미 만들어놓은 `scanner_screen.dart` 구조를 그대로 사용하므로 앱이나 홈페이지를 새로 만들 필요 없음.

---

## 전체 흐름 한눈에 보기

```
[QR코드 이미지]          [불침번 앱]              [라즈베리파이 서버]
   "EXT-01"    --스캔-->  코드 읽음  --조회요청-->  /api/equipment/EXT-01
                                    <--결과 응답--   {"name": "1층 분말소화기",
                                                       "status": "정상",
                                                       "last_check": "2024-01-10", ...}
                        화면에 표시
```

QR코드 자체에는 데이터가 하나도 안 들어있고, **오직 "EXT-01"이라는 짧은 이름표 역할만** 합니다. 실제 점검이력·현재상태 데이터는 전부 라즈베리파이 서버(DB)에 있고, 앱이 그때그때 물어봐서 최신 정보를 가져옵니다.

**이 방식의 핵심 장점**: QR코드를 인쇄해서 붙인 뒤에 점검이력이 바뀌어도 QR코드를 다시 인쇄할 필요가 없습니다. 서버 쪽 데이터만 갱신하면 됩니다.

---

## STEP 1. 설비 ID 규칙 정하기

QR코드에 넣을 문자열 형식을 먼저 정합니다. 예시:

| 설비 종류 | ID 형식 | 예시 |
|---|---|---|
| 소화기 | `EXT-XX` | EXT-01, EXT-02 |
| 스프링클러 | `SPR-XX` | SPR-01, SPR-02 |
| 유도등 | `LGT-XX` | LGT-01, LGT-02 |
| 자탐(감지기) | `DET-XX` | DET-01, DET-02 |

이 ID가 나중에 서버 DB에서 설비를 찾는 **기본 키(key)** 역할을 하므로, 한번 정하면 되도록 바꾸지 않는 게 좋습니다.

---

## STEP 2. 파이썬 라이브러리 설치

**라즈베리파이 또는 QR코드를 만들 PC**에서 실행:

```bash
pip install qrcode[pil]
```

---

## STEP 3. QR코드 생성 스크립트 작성

**새 파일**: `generate_qr.py` (서버 프로젝트 폴더 아무 곳에나)

```python
import qrcode
import os

# 여기에 실제 설비 ID 목록을 넣기
equipment_ids = [
    "EXT-01",
    "EXT-02",
    "SPR-01",
    "SPR-02",
    "LGT-01",
    "DET-01",
]

# 저장할 폴더 만들기
output_dir = "qr_codes"
os.makedirs(output_dir, exist_ok=True)

for eid in equipment_ids:
    img = qrcode.make(eid)  # QR코드 안에는 이 문자열만 들어감
    filepath = os.path.join(output_dir, f"{eid}.png")
    img.save(filepath)
    print(f"생성 완료: {filepath}")

print(f"\n총 {len(equipment_ids)}개 QR코드 생성 완료 → {output_dir}/ 폴더 확인")
```

## STEP 4. 실행

```bash
python generate_qr.py
```

`qr_codes/` 폴더에 `EXT-01.png`, `EXT-02.png` ... 이런 식으로 이미지 파일들이 생성됩니다.

---

## STEP 5. 라벨로 인쇄해서 부착

- 생성된 PNG 파일들을 라벨 프린터나 일반 프린터로 인쇄
- 되도록 QR코드 아래에 사람이 읽을 수 있는 텍스트도 같이 인쇄 권장 (예: "EXT-01" 글자를 QR코드 밑에 작게)
  - 스캔이 안 되는 비상 상황에서도 수기로 어떤 설비인지 확인 가능
- 해당 설비(소화기, 스프링클러 밸브 등) 옆에 부착

```python
# 텍스트까지 같이 넣고 싶다면 (선택사항)
from PIL import Image, ImageDraw, ImageFont

for eid in equipment_ids:
    qr_img = qrcode.make(eid).convert("RGB")
    w, h = qr_img.size

    # 아래쪽에 텍스트 공간 추가
    new_img = Image.new("RGB", (w, h + 30), "white")
    new_img.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(new_img)
    draw.text((w // 2 - 20, h + 5), eid, fill="black")

    new_img.save(os.path.join(output_dir, f"{eid}.png"))
```

---

## STEP 6. 서버(api.py) — 조회 API 확인/추가

`scanner_screen.dart`가 호출하는 주소가 `/api/equipment/<코드>` 이므로, `api.py`에 이 라우트가 있는지 확인합니다. 없다면 추가:

```python
from flask import jsonify

# 임시 DB (나중엔 실제 DB로 교체)
equipment_db = {
    "EXT-01": {
        "name": "1층 로비 분말소화기",
        "status": "normal",
        "last_check": "2024-01-10",
        "next_check": "2024-04-10",
        "history": [
            {"date": "2024-01-10", "result": "정상", "checker": "홍길동"},
            {"date": "2023-10-10", "result": "정상", "checker": "홍길동"},
        ]
    },
    "SPR-01": {
        "name": "2층 스프링클러 밸브",
        "status": "warning",
        "last_check": "2024-01-05",
        "next_check": "2024-02-05",
        "history": [
            {"date": "2024-01-05", "result": "주의 - 압력 낮음", "checker": "김철수"},
        ]
    },
    # ... 나머지 설비도 등록
}

@app.route('/api/equipment/<eid>', methods=['GET'])
def get_equipment(eid):
    equipment = equipment_db.get(eid)
    if equipment is None:
        return jsonify({"error": "존재하지 않는 설비 ID입니다"}), 404
    return jsonify(equipment)
```

**주의**: 위 STEP 1에서 정한 ID(`EXT-01` 등)와 `equipment_db`의 키가 정확히 일치해야 합니다. 대소문자나 하이픈 위치가 다르면 "설비를 찾을 수 없음" 에러가 뜹니다.

---

## STEP 7. 앱에서 확인

이미 만들어놓은 `scanner_screen.dart` 구조 그대로 작동합니다:

```
QR코드 스캔 → "EXT-01" 읽음 → http://<서버IP>:5001/api/equipment/EXT-01 호출
→ 서버가 위 equipment_db에서 EXT-01 찾아서 JSON 응답
→ 앱 화면에 설비명, 상태, 점검이력 표시
```

폰 브라우저로 먼저 `http://<서버IP>:5001/api/equipment/EXT-01` 직접 접속해서 JSON이 뜨는지 확인 후, 실제 QR코드 인쇄물을 앱으로 스캔해보는 순서를 권장합니다.

---

## 한눈에 순서 요약

```
1. 설비 ID 형식 정하기          (EXT-01, SPR-01 등 규칙)
2. qrcode 라이브러리 설치        (pip install qrcode[pil])
3. generate_qr.py 작성 및 실행   (PNG 이미지 일괄 생성)
4. 인쇄 후 설비에 부착
5. api.py에 /api/equipment/<id> 라우트 확인/추가
6. equipment_db에 각 설비 데이터 입력
7. 앱으로 실제 스캔 테스트
```

---

## 나중에 데이터가 늘어나면

지금은 `equipment_db`가 파이썬 코드 안에 딕셔너리로 하드코딩되어 있습니다. 설비 개수가 많아지거나 점검이력을 수시로 갱신해야 한다면, 이 부분을 SQLite 같은 실제 DB 파일로 옮기는 걸 고려하면 좋습니다. 지금 단계(프로토타입)에서는 하드코딩으로 충분합니다.
