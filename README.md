# K-IDOL ARCHIVE · Alpha 1

Python/PySide6 기반 로컬 인물 사진 라이브러리입니다. 사진 중심 차콜 UI, 그룹·인물 관리, 비동기 갤러리와 기본 뷰어를 제공합니다. Eagle의 라이브러리/정보 패널 구조와 Lightroom의 감상 흐름을 참고한 독립 구현입니다. 상표·아이콘·사진·소스는 복제하지 않았습니다.

## 실행

Python 3.12 권장 (테스트: Linux Python 3.12.14, PySide6 6.8.3, Pillow 11.2.1).

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

데스크톱 세션이 필요합니다. GUI 테스트는 `QT_QPA_PLATFORM=offscreen`으로 가능합니다. Linux 기본 Qt xcb 실행에는 배포판별 X11/xcb 시스템 라이브러리가 필요할 수 있습니다. `python -m app.main`을 프로젝트 루트에서 실행하세요.

## 사용

- 왼쪽 ARTISTS의 **＋**에서 그룹·인물·솔로를 추가합니다. 항목 우클릭으로 이름과 별칭을 수정합니다.
- **멤버 연결 관리**에서 여러 그룹 소속 및 혼성그룹의 기본 갤러리 대상을 관리합니다. 소속 전체와 갤러리 대상은 별개입니다.
- **사진 가져오기**에서 다중 파일 또는 폴더를 선택합니다. 하위 폴더까지 검사하며 심볼릭 링크 폴더는 따라가지 않습니다.
- 기본은 **외부 파일 참조**입니다. 원본 복사를 선택하면 영구 저장소에 해시 이름으로 보관합니다. 원본을 수정·재압축하지 않습니다.
- 가져오기 때 선택한 그룹·인물은 전체 선택 사진에 적용됩니다. 확인한 인물만 연결하세요. 사진 정보 패널에서도 추가 연결할 수 있습니다.
- 중복은 SHA256으로 판정합니다. 같은 사진을 재등록하면 파일을 중복 저장하지 않고 그룹·인물 연결을 추가합니다.
- 파일명·그룹명·인물명·별칭 검색, 등록일/파일명/해상도 정렬, 즐겨찾기를 지원합니다. 촬영일을 추정해 채우지 않습니다.
- 더블클릭하면 뷰어: 휠 확대, 드래그 이동, 좌우 방향키, Esc 복귀, F11 전체화면. `Ctrl+0` 화면 맞춤, `Ctrl+1` 원본 픽셀 로드.
- 기본 뷰어는 긴 변 4096px 미리보기입니다. **원본 픽셀**에서 전체 해상도를 비동기로 읽습니다. 미리보기 여부를 화면에 표시합니다. 최대 1억 픽셀 안전 한도가 있습니다. 애니메이션은 첫 프레임, 다중 페이지 TIFF는 첫 페이지만 지원합니다. 색상 관리/HDR/전문 타일 뷰어는 후속 단계입니다.
- 누락 파일은 오류 상태로 표시합니다. **원본 다시 연결**에서 동일 SHA256 파일로 복구합니다.
- `Ctrl+K` 검색, `Ctrl+O` 파일 가져오기. 좁은 창에서는 정보 패널이 자동으로 접히며 버튼으로 펼칠 수 있습니다.

## 데이터 보호

Windows 기본 데이터: `%LOCALAPPDATA%\K-IDOL-ARCHIVE`. Linux: `~/.local/share/K-IDOL-ARCHIVE`. 개발/테스트는 비밀값이 아닌 `K_IDOL_DATA_DIR`로 격리 가능합니다.

DB, 설정, 썸네일, 영구 원본, 로그를 분리합니다. 설정에서 캐시와 영구 저장 경로를 바꿀 수 있으며 기존 파일은 자동 이동/삭제하지 않습니다. 캐시 정리 UI는 아직 제공하지 않습니다. DB는 외래키·트랜잭션·WAL 및 버전 마이그레이션을 사용합니다. 이름은 기본키가 아니며 동일 이름은 자동 병합하지 않습니다. 현재 동명이인은 구분되는 표시 이름으로 등록합니다. **업데이트 전에 앱을 닫고 데이터 폴더를 백업하세요.**

## 테스트와 측정

```sh
python -m pytest -q
python scripts/benchmark.py
python scripts/profile_gallery.py
python scripts/capture_ui.py
```

Linux 헤드리스: 앞에 `QT_QPA_PLATFORM=offscreen`을 지정하세요. 테스트 데이터는 임시 폴더에 생성한 이미지이며 실제 아티스트 사진을 포함하지 않습니다. `docs/benchmark.json`은 100/1,000/10,000개 가상 메타데이터 조회, `docs/image-profile.json`은 100개의 생성 JPEG 로딩 측정입니다. 실측 범위와 미검증 사항은 `docs/test-report.md`를 확인하세요.

## Windows 배포

Windows Python 3.12에서 `build_windows.bat` 실행. 테스트 → PyInstaller 폴더형 빌드 → 의존성 라이선스 수집 → ZIP 및 SHA256 생성 순서입니다. 결과 경로: `dist/K-IDOL-ARCHIVE_v0.1.0_Windows.zip`.

**현재 Linux 환경에서는 Windows 실행 파일을 생성하거나 실기 검증하지 않았습니다.** 빌드 스크립트를 제공하지만 Windows 성공을 보증하지 않습니다. 소스 ZIP은 `python scripts/package.py`로 생성합니다. 사용자 데이터는 배포 폴더 밖에 저장됩니다.

## 단계 범위

온라인 수집·신규 아티스트 발견·AI 구도/색상 분석·연관 사진·업스케일은 **미구현**입니다. 확장 계약과 버전/근거를 기록할 확장 데이터 저장 구조만 준비했습니다. 외부 제공처는 활성화하지 않았고, 최신 이용 정책·모델 라이선스 검증은 실제 도입 시 수행해야 합니다. UI에 가짜 AI 결과나 작동하는 척하는 버튼은 없습니다.

최적화: 256개 단위 비동기 조회, 모델/델리게이트 기반 리스트, batched 레이아웃, 화면 주변 썸네일 우선, 최대 8개 썸네일 작업/2개 처리 스레드, decoded thumbnail LRU 96개, 뷰어 이전 작업 취소와 오래된 응답 무시. 전체 결과의 메타데이터는 스크롤에 따라 누적되므로 무한 규모 메모리 고정을 보장하지 않습니다. 전체 해상도 디코딩은 이미지 크기에 비례하는 메모리가 필요합니다.
