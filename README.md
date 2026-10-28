## 최신 Windows 다운로드 · 검색 연결 실험판

**Windows 보고서의 미리보기 차단 수정:** [검색 출처]에 **사이트 제공 파일 다운로드·미리보기** 선택지를 추가했습니다. 파일 다운로드 이용 조건을 확인한 경우 이 옵션을 켜고 새로 검색하세요. 차단된 이미지 변환 경로 대신 사이트가 실제 제공한 CDN 주소를 사용하며, CDN 정책과 기존 사진별 거부 설정을 유지합니다. 자동 영구 보관·분석·AI는 실행하지 않습니다. 전체 검사 225개 통과·24개 건너뜀. 실제 Windows 사진 표시는 수정본에서 추가 확인이 필요합니다.

[ZIP 다운로드](https://github.com/wkion154/-/raw/refs/heads/k-idol-archive/releases/K-IDOL-ARCHIVE_Search_Stage1_Experimental.zip)

전체를 새 폴더에 압축 해제하고 `START_WINDOWS.bat`를 실행하세요. **Python 3.12와 Edge 또는 Chrome이 필요한 소스 실행판**이며 EXE 설치판은 아닙니다. 기존 데이터는 삭제하지 마세요.

이번 수정은 실제 화면의 인물/그룹 필터 확인, 검색어와 구도·색상 힌트 분리, 추가 페이지·차단 상태 처리입니다. 앱에서 출처를 설정한 후 닫고 `CHECK_SEARCH_WINDOWS.bat`를 실행하면 같은 폴더에 `search-pipeline-report.json`이 생성됩니다. 기본 검사는 영구 보관이나 권한 자동 부여를 하지 않습니다.

검증: 전체 테스트 218개 통과·24개 건너뜀, Linux 앱 기본 실행 및 ZIP 무결성 통과. **실제 Windows 검색/화면 동작은 미검증**입니다. Flickr는 현재 robots 정책에서 앱 접근이 차단됩니다. 1단계는 계속 진행 중입니다. 자세한 내용은 [Windows 실행 안내](WINDOWS_SEARCH_START.txt)를 확인하세요.

---

**검색 개편 1단계 Windows 앱 실험 ZIP:** [다운로드](https://github.com/wkion154/-/raw/refs/heads/k-idol-archive/releases/K-IDOL-ARCHIVE_Search_Stage1_Experimental.zip). 기존 앱에 Kpopping 브라우저 검색과 Flickr 공개 태그 피드 경로, 스크롤 추가 검색을 연결했습니다. `WINDOWS_SEARCH_START.txt`를 먼저 읽으세요. Flickr는 검색어별 최근 최대 20개이며 전체 이미지 검색 API가 아닙니다. 실제 Windows 앱 성공·다중 사이트 사진 표시·원본 확인은 아직 미검증입니다. Python으로 실행하는 실험용 소스 ZIP이며 EXE 또는 정식 완료판이 아닙니다.

**Windows 접속 확인 도구:** [진단 ZIP 다운로드](https://github.com/wkion154/-/raw/refs/heads/k-idol-archive/releases/K-IDOL-ARCHIVE_Windows_Search_Check.zip). 압축 해제 후 `START_CHECK.bat` 실행 → 이름 입력 → 브라우저 검색 결과 확인 → 생성된 `search-report.json`을 대화에 첨부하세요. Python 3.12와 Edge/Chrome이 필요합니다. 진단 도구이며 완성된 검색 앱이 아닙니다. Windows 실기 미검증.

> **온라인 검색 중심으로 방향 수정 중 — 1단계 미완료.** 실제 이름 검색 어댑터와 기존 갤러리 연결, 권한을 확인한 사이트 제공 파일의 실측·뷰어·보관 코드를 추가했습니다. 실사이트 통합 검증은 아직 완료하지 못했습니다. [단계별 현황](docs/development-status.md) · [구현·검증 기록](docs/search-realignment-stage1.md). 현재 1단계 마무리가 필요하며 이후 2·3단계가 남아 있습니다. 아래 v0.7.4 ZIP은 방향 수정 전 버전이며 이번 검색 변경을 포함하지 않습니다.

# K-IDOL ARCHIVE · 7-4 개발판 · v0.7.4
**다운로드:** [Windows 소스 실행용 ZIP](https://github.com/wkion154/-/raw/refs/heads/k-idol-archive/releases/K-IDOL-ARCHIVE_v0.7.4_Windows_Source.zip) · [SHA256](releases/K-IDOL-ARCHIVE_v0.7.4_Windows_Source.zip.sha256)

ZIP을 전부 압축 해제하고 Python 3.12 설치 후 `START_WINDOWS.bat`를 실행하세요. 첫 실행은 의존성을 다운로드합니다. 이 파일은 EXE 설치판이 아니며 Windows 실행 검증이 남은 개발판입니다.

Python/PySide6 기반 사진 아카이브입니다. 차콜 다크 UI, 그룹·인물 관리, 가상 갤러리, 고해상도 뷰어와 온라인 수집 구조를 제공합니다. Eagle/Lightroom의 라이브러리·감상 흐름을 참고한 독립 구현이며 상표·아이콘·사진·소스를 복제하지 않았습니다.

**7-4 배포 검증 준비:** 격리된 Windows 빌드, EXE 자동 실행 검사, 배포 ZIP 무결성 검사와 수동 CI를 추가했습니다. Windows에서 실제 실행한 결과는 아직 없습니다.

**7-3 기능: 실제 FSRCNN 2배·4배 업스케일과 원본 좌표를 공유하는 3중 비교를 추가했습니다.** 참조 모드는 확인된 같은 인물의 얼굴 크기·방향·눈/입 상태를 검사하고 얼굴 내부만 제한적으로 보조하는 실험 기능입니다. 다양한 촬영 사진의 정체성 보존을 검증한 범용 인물 참조 모델은 아닙니다.

[7-4 검증 보고서](docs/stage-7-4-report.md) · [7-3 기능 보고서](docs/stage-7-3-report.md) · [3중 비교](docs/upscale-compare-v0.7.3.png) · [슬라이더 비교](docs/upscale-slider-v0.7.3.png) · [7-2 색상 탐색](docs/stage-7-2-report.md)

사진 정보 패널 또는 뷰어에서 **AI 업스케일**을 실행합니다. 소스 환경에 `python -m pip install -r requirements-upscale.lock`로 선택 의존성을 설치하세요. 얼굴 참조는 `python -m pip install -r requirements-reference.lock`도 설치합니다. 작은 FSRCNN 가중치 2개와 라이선스를 동봉하므로 실행 시 모델을 내려받지 않습니다. 사진별 분석·AI 입력·결과 저장 권한을 각각 확인해야 합니다. 원격 후보는 먼저 원본 영구 보관과 출처 권한 검토가 필요합니다. 입력을 외부로 전송하지 않습니다.

색상·구도 탐색은 **필터 · 이미지 분석**에서 사용합니다. 구도 모델 선택 설치는 `requirements-analysis.lock`입니다. 실제 Wikimedia 연결, 실제 인물 정확도, RTX 4080 SUPER/GPU 및 Windows 실행·EXE 검증은 남아 있습니다. CPU가 기본이며 CUDA 진단을 통과한 환경에서만 GPU를 선택할 수 있습니다(실제 GPU 미검증). 기본 Windows 빌드는 선택 CV 기능을 제외합니다.

## 실행

Python 3.12 권장. 검증 환경: Linux Python 3.12.14 / PySide6 6.8.3 / Pillow 11.2.1.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -m app.main
```

Linux 개발:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m app.main
```

프로젝트 루트에서 실행하세요. 데스크톱 세션이 필요하며 Linux Qt xcb 실행은 배포판별 X11/xcb 라이브러리가 필요할 수 있습니다. 테스트는 `QT_QPA_PLATFORM=offscreen`을 사용할 수 있습니다.

## 사진 모으기와 탐색

- 왼쪽 ARTISTS **＋**에서 그룹·인물·솔로를 등록하고 우클릭으로 이름/별칭을 수정합니다. **멤버 연결 관리**는 전체 소속과 기본 갤러리 대상을 구분합니다.
- **사진 가져오기**에서 파일/폴더를 선택합니다. 기본은 외부 파일 참조, 원본 복사는 영구 저장소에 해시 이름으로 보관합니다. 사진을 변형·재압축하지 않습니다. SHA256이 같은 사진은 같은 자산에 인물/그룹 연결을 추가합니다.
- 상단 **로컬 + 온라인 / 로컬만 / 온라인 후보만**을 선택합니다. 온라인 후보만은 아직 로컬 등록본과 연결되지 않은 항목입니다. 로컬 라이브러리는 등록된 사진을 표시합니다.
- 온라인 후보는 실제 파일 보유를 뜻하지 않습니다. 표시 권한이 없는 후보는 **출처 링크**, 해상도를 측정하지 않은 후보는 **미실측**으로 표시합니다. 제공처 선언과 캐시 실측은 구분합니다.
- 화면 주변의 허용된 썸네일만 비동기로 받습니다. 일반 페이지의 임의 원본을 썸네일 대신 받지 않습니다.
- 파일명·확인된 인물/별칭·그룹 검색, 등록/발견순·파일명·실측 해상도 정렬과 출처 필터를 지원합니다. 게시일/등록일을 촬영일로 채우지 않습니다.
- 온라인 사진도 즐겨찾기와 명시적 인물/그룹 연결을 기록할 수 있습니다. 제목 언급·그룹/카테고리 분류는 실제 등장 인물 확정이 아닙니다.
- **원본 영구 보관**은 별도 요청입니다. 보기만 했을 때는 영구 저장소에 등록하지 않습니다. 보관 시 즐겨찾기/확인 연결을 전달하고 같은 자산의 타일 중복을 줄입니다. 기존 외부 참조를 보관본으로 전환해도 기존 파일은 그대로 두고 이전 경로를 기록합니다.
- 실제 파일 내용이 바뀌면 이전 인물 확인/즐겨찾기를 이력에 보존하고 새 파일에는 재적용하지 않습니다. 이미 저장한 원본·연결은 유지합니다.

## 뷰어와 연관 사진

더블클릭으로 진입합니다. 온라인 원본은 해당 사진을 열 때 원본 이용 권한을 확인하고 필요한 파일만 보기 캐시로 받습니다. 다음 원격 원본을 미리 받지 않습니다. URL이 만료되어도 권한/개정본/무결성이 일치하는 확보된 캐시는 재사용할 수 있습니다.

휠 확대, 드래그 이동, 좌우 탐색, Esc 복귀, F11 전체화면. `Ctrl+0` 맞춤, `Ctrl+1` 원본 픽셀, `W/H` 너비/높이 맞춤. 전체화면 조작부는 잠시 후 숨습니다. 같은 정렬/필터 결과의 로컬·온라인 항목을 순회하며 원본을 열 수 없는 항목도 목록에서 삭제하지 않습니다.

기본은 긴 변 4096px 미리보기, 원본 픽셀은 전체 해상도 비동기 로딩입니다. 최대 1억 픽셀 안전 한도가 있으며 애니메이션/다중 TIFF는 첫 프레임/페이지만 지원합니다. 색상 관리/HDR/영역 디코딩 전문 뷰어는 후속 범위입니다. 제공된 파일 전체 해상도와 촬영 원본 여부는 별개입니다.

보관된 사진에서 **연관 사진 찾기**를 사용할 수 있습니다. 확인 인물·날짜·그룹·출처와 허용된 기존 dHash 특징을 활용하며 근거와 규칙 점수를 표시합니다. **동일 행사 확인 / 연결 / 제외 / 해제**는 원본과 별도 기록입니다. 뷰어의 **미리보기**는 기본 숨김/최대 12장의 연관 필름스트립입니다.

연관 사진 창의 참조 후보 검색은 모델을 실행하지 않습니다. 별도 AI 업스케일 창은 실제 FSRCNN을 실행하고, 참조 모드에서 권한·확인된 인물·얼굴 크기·방향/표정·정렬·선명도를 검사합니다. 이 자동 검사는 정체성 보존을 입증하지 않습니다.

## 출처와 권한

온라인 → **출처 · URL**에서 검토한 도메인 설정과 HTTPS 링크를 등록합니다. 메타데이터·썸네일·원본 다운로드·로컬 분석·AI 입력·결과 저장은 독립 권한이며 브라우저 DOM 실행도 별도 허용이 필요합니다. 로컬 사본을 추가했다고 원격 출처의 조건을 우회하지 않습니다.

Wikidata/Commons, 일반 HTML/직접 URL 및 선택적 DOM 어댑터가 있습니다. **X·Instagram·Flickr·Kpopping·언론·공식·팬 사이트는 검토 후보이며 실제 연동 완료를 뜻하지 않습니다.** 외부 실행 코드 플러그인은 가져오지 않고 JSON 설정만 검증합니다. 예시: `docs/examples/public-page-source.json`.

선택적 JavaScript DOM 처리:

```sh
python -m pip install -r requirements-browser.lock
python -m playwright install chromium
```

브라우저 샌드박스를 해제하지 마세요. Windows 기본 빌드 스크립트는 선택 브라우저를 자동 포함하지 않습니다. 로그인/캡차/접근 제한 우회, 워터마크 제거, 캡처의 원본 위장은 구현하지 않습니다.

현재 정책 원문/robots/API 조건과 라이선스를 실제로 검토한 뒤 개발/관리 절차로 기록합니다:

```sh
python scripts/check_sources.py --output docs/current-source-check.json
python scripts/check_sources.py --output docs/current-source-check.json --record-review --review-note "실제로 검토한 범위와 근거"
```

기본 기록은 메타데이터 권한만 적용합니다. 다운로드만으로 약관 검토나 사진 이용권이 확보됐다고 간주하지 않습니다. 다른 작업은 확인한 범위만 별도로 허용하세요. 수집 커버리지는 제한적이며 모든 아티스트/사진의 완전 수집을 보장하지 않습니다.

## 처리량과 데이터 보호

사진 감상/연관 화면 또는 스크롤 중 새 자동 작업은 대기합니다. 실행 중 작업과 수동 요청은 유지합니다. 설정 **백그라운드 수집**에서 출처 1개씩/최대 2개를 선택할 수 있으며 분석은 별도 1개씩 처리합니다. 서버의 재시도 대기는 재시작/수동 요청으로 건너뛰지 않습니다. 자동 갱신을 껐다면 복구된 작업도 직접 요청/재시도합니다.

Windows 기본 데이터: `%LOCALAPPDATA%\K-IDOL-ARCHIVE`. Linux: `~/.local/share/K-IDOL-ARCHIVE`. 개발 데이터는 `K_IDOL_DATA_DIR`로 격리할 수 있습니다. DB/설정/썸네일/보기 캐시/영구 원본/로그를 구분합니다. 캐시와 영구 경로 변경은 기존 파일을 자동 이동·삭제하지 않습니다. 캐시 정리 UI는 후속 작업입니다.

**업데이트 전 앱을 닫고 전체 데이터 폴더를 별도 백업하세요.** v0.7.1은 스키마 v6이며 v1~v5 DB에 고유 SQLite 백업을 남기고 원자적으로 이전합니다. 이전 앱은 새 DB를 열 수 없습니다. 같은 데이터 폴더는 한 앱만 열도록 잠금을 적용했습니다.

메타데이터 최대 2,048건, 썸네일 LRU 96개, 뷰어 메타데이터 창 768건, 이미지 캐시 128MiB/12개입니다. 현재 표시 이미지·네이티브 디코더 임시 버퍼는 캐시 한도 밖에 있습니다. 실행 중 네이티브 디코딩/DNS/소켓을 즉시 중단하거나 전체 프로세스 메모리를 절대 한도로 제한하지는 않습니다.

## 검증과 배포

Linux에서 **140개 테스트 통과**, 실제 Chromium 샌드박스의 합성 DOM 테스트 포함. 5만 로컬/5만 온라인 메타데이터 혼합 갤러리 마지막 페이지 이동 26.38ms, 이벤트 루프 최대 간격 19.37ms. 한 생성 이미지와 가상 메타데이터를 사용한 offscreen 측정이며 실제 수집/10만 고유 사진/화면 FPS 검증이 아닙니다.

```sh
python -m pytest -q
python scripts/profile_hybrid_gallery.py
python scripts/profile_virtual_gallery.py
python scripts/capture_hybrid.py
python scripts/package.py
```

Windows Python 3.12의 `build_windows.bat`는 테스트 → PyInstaller 폴더형 빌드 → 라이선스 수집 → ZIP/SHA256 생성 절차입니다. 예상 출력 `dist/K-IDOL-ARCHIVE_v0.7.1_Windows.zip`. **현재 Linux에서 Windows 실행 파일을 생성하거나 실기 검증하지 않았습니다.** 제공 결과물은 소스 ZIP입니다. 실제 사이트 검증과 Windows/AI 출시 조건은 [7-1 보고서](docs/stage-7-1-report.md)에 정리했습니다.


## 배포 파일 확인

`python scripts/verify_package.py dist/K-IDOL-ARCHIVE_v0.7.4_Source.zip`로 파일 목록과 해시를 검증합니다. Windows 기본 빌드는 `build_windows.bat`를 실행합니다. `build/windows-venv` 생성 환경을 재생성하며 사용자 `.venv`는 사용하지 않습니다. 기본 EXE는 AI 제외판입니다. 실행 검사와 Windows 실기 확인의 차이는 [7-4 보고서](docs/stage-7-4-report.md)에 정리했습니다.
