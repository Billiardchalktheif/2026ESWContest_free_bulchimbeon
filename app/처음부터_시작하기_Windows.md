# 불침번 앱 — Windows 처음부터 시작하기

지금까지 받은 파일들(`main.dart`, `api.py`, `pubspec.yaml`, `generate_qr_codes.py`, 가이드 md들)을 실제로 켜보는 전 과정입니다. 순서대로만 따라가면 됩니다. 막히는 단계 있으면 그 단계 이름이랑 에러 메시지 그대로 알려주세요.

---

## 0단계 — 지금 뭘 하는 건지 (30초 요약)

```
[내 Windows PC 또는 라즈베리파이] ── api.py 실행 (서버) ──┐
                                                          │ 같은 와이파이
[내 안드로이드 폰] ── Flutter 앱 실행 ──────────────────────┘
```

먼저 **서버(Python)를 PC에서 켜서 확인** → 그 다음 **Flutter 앱을 폰에 설치해서 서버랑 연결**하는 순서로 갑니다. 나중에 서버는 라즈베리파이로 옮기면 되고, 지금은 Windows PC에서 테스트해도 무방합니다 (목데이터 모드라 어차피 진짜 센서 없이도 동작함).

---

## PART 1. 서버(api.py) 먼저 켜보기

### 1-1. Python 설치 확인
```powershell
python --version
```
버전이 뜨면 설치되어 있는 것. 안 뜨면 https://www.python.org/downloads/ 에서 설치 (설치 시 "Add Python to PATH" 체크박스 꼭 체크).

### 1-2. Flask 설치
```powershell
pip install flask reportlab qrcode[pil]
```

### 1-3. 폴더 준비하고 api.py 넣기
```powershell
mkdir C:\bulchimbeon\server
```
받은 `api.py` 파일을 `C:\bulchimbeon\server\api.py` 로 옮기기.

### 1-4. 서버 실행
```powershell
cd C:\bulchimbeon\server
python api.py
```
아래처럼 뜨면 성공:
```
[불침번 API v2] MOCK_MODE=True - http://0.0.0.0:5001/api/status
```

### 1-5. 브라우저로 확인
PC에서 `http://localhost:5001/api/status` 열어서 JSON 데이터가 보이면 서버 준비 완료. 이 창은 계속 켜두세요 (닫으면 서버 꺼짐).

### 1-6. 내 PC의 IP 주소 확인 (이거 나중에 꼭 씁니다)
새 PowerShell 창을 하나 더 열고:
```powershell
ipconfig
```
"무선 LAN 어댑터 Wi-Fi" 밑에 있는 **IPv4 주소** (예: `192.168.0.15`)를 메모해두세요.

폰에서도 `http://그_IP:5001/api/status` 로 접속되는지 확인하세요 (폰이 PC랑 같은 와이파이여야 함). 여기서 안 되면 Windows 방화벽이 막고 있을 가능성이 큽니다 — 아래 참고.

<details>
<summary>방화벽 때문에 막히는 경우 (펼쳐서 확인)</summary>

Windows 방화벽 설정 → "고급 설정" → "인바운드 규칙" → "새 규칙" → 포트 → TCP 5001 → 연결 허용. 또는 간단히 python.exe 자체를 방화벽 허용 목록에 추가하세요.
</details>

---

## PART 2. Flutter 설치

### 2-1. Flutter SDK 다운로드
https://docs.flutter.dev/get-started/install/windows 접속 → Windows용 zip 다운로드 → `C:\flutter` 에 압축 풀기 (경로에 한글/공백 없어야 함)

### 2-2. 환경변수(PATH) 등록
1. 시작 메뉴에서 "환경 변수" 검색 → "시스템 환경 변수 편집" 열기
2. "환경 변수" 버튼 → 사용자 변수의 `Path` 선택 → 편집
3. 새로 만들기 → `C:\flutter\bin` 추가 → 확인 눌러서 다 닫기
4. **새 PowerShell 창을 열어서** (기존 창은 반영 안 됨):
   ```powershell
   flutter --version
   ```
   버전 정보가 뜨면 성공

### 2-3. Android Studio 설치 (안드로이드 폰 빌드에 필요)
https://developer.android.com/studio 에서 다운로드 및 설치. 설치 중 "Android SDK", "Android Virtual Device" 항목 체크된 채로 진행.

### 2-4. 상태 점검
```powershell
flutter doctor
```
아래 항목들 위주로 초록색(✓)이면 충분합니다 (전부 다 초록일 필요 없음):
- Flutter
- Android toolchain
- Connected device (폰 연결 후에 체크됨, 지금은 빨간불이어도 정상)

빨간 X 뜨는 항목 있으면 그 메시지 캡처해서 보여주세요.

---

## PART 3. Flutter 프로젝트 만들고 파일 넣기

### 3-1. 프로젝트 생성
```powershell
cd C:\bulchimbeon
flutter create bulchimbeon_app
cd bulchimbeon_app
```

### 3-2. 받은 파일 덮어쓰기
- 받은 `pubspec.yaml` → `C:\bulchimbeon\bulchimbeon_app\pubspec.yaml` 덮어쓰기
- 받은 `main.dart` → `C:\bulchimbeon\bulchimbeon_app\lib\main.dart` 덮어쓰기

### 3-3. 서버 주소 연결
`lib\main.dart` 파일을 메모장이나 VS Code로 열어서, 맨 위쪽 이 줄을 찾아:
```dart
const String API_BASE_URL = "http://192.168.0.10:5001/api";
```
`192.168.0.10` 부분을 **1-6에서 확인한 내 PC의 실제 IP 주소**로 바꾸고 저장.

### 3-4. 카메라 권한 추가 (QR 스캔용)
`android\app\src\main\AndroidManifest.xml` 파일 열어서 `<manifest ...>` 태그 바로 아래 줄에 추가:
```xml
<uses-permission android:name="android.permission.CAMERA" />
```

### 3-5. 패키지 설치
```powershell
flutter pub get
```

---

## PART 4. 폰에 설치해서 실행

### 4-1. 폰 개발자 모드 켜기
안드로이드 폰에서: 설정 → 휴대전화 정보 → "빌드 번호" 7번 연속 탭 → "개발자 옵션" 생김 → 그 안에서 "USB 디버깅" 켜기

### 4-2. USB로 연결
폰을 PC에 USB 케이블로 연결 → 폰에 "USB 디버깅을 허용하시겠습니까" 팝업 뜨면 허용

### 4-3. 인식 확인
```powershell
flutter devices
```
폰 이름이 목록에 뜨면 인식된 것.

### 4-4. 실행
```powershell
flutter run
```
처음엔 빌드하느라 3~5분 정도 걸립니다. 끝나면 폰에 앱이 자동으로 설치되고 실행됩니다.

---

## PART 5. 동작 확인 체크리스트

- [ ] **상태 탭**: 6개 카드에 정상/주의/경보 색상 표시되는지
- [ ] **QR조회 탭**: 카메라 화면이 뜨는지 (아직 QR 없어도 화면만 떠도 성공)
- [ ] **알림 탭**: 목록 뜨고, "확인" 버튼 누르면 "확인됨"으로 바뀌는지
- [ ] **도구 탭 → PDF 생성**: 누르면 PDF 다운로드/열람 되는지
- [ ] **도구 탭 → 시험 시작**: 누르면 로딩 후 결과 팝업 뜨는지

전부 되면 기본 세팅 끝입니다. 여기서부터 QR코드 인쇄(`generate_qr_codes.py`)나 실제 하드웨어 연동은 다음 단계로 넘어가면 됩니다.

---

## 막히면 이렇게 알려주세요

"몇 단계에서, 무슨 명령어 쳤을 때, 어떤 에러 메시지 떴는지" 이 세 가지만 그대로 복붙해서 보내주시면 바로 원인 짚어드릴게요.
