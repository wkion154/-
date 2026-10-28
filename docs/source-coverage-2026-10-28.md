# 다중 출처 조사와 연결 상태

목표는 한 사이트의 갤러리를 여는 데 그치지 않고 여러 사이트의 사진을 검색·표시·보관 경로로 연결하는 것이다. Wikimedia는 이전 지시에 따라 통합 검색에서 계속 제외한다. 접속 가능, 검색 성공, 사진 디코딩, 원본 보관, Windows 동작을 별도로 기록한다.

| 출처 | 실제 확인 | 현재 연결/남은 일 |
|---|---|---|
| Kpopping | 사용자 Windows에서 장원영 사진 표시. 이서는 표시 모드에서 검색 입력란 미발견 | 기존 연결 유지. 다른 이름/그룹 선택과 숨김 모드 접근은 미완료 |
| DBKpop | 공개 검색 HTML·WordPress API 200. 실제 Leeseo 검색 2배치 143개 후보, 3개 미리보기 디코딩 성공 | 이번에 앱 연결. API 키·별도 브라우저 불필요. 영문 검색 권장. 원본은 게시물이 제공한 파일 주소와 별도 권한 경로 사용 |
| 네이버 이미지 검색 | 공식 문서 200. 비로그인 API는 앱 Client ID/Secret 필요, 검색 API 일일 25,000회 한도 안내 | 이번에 API 연결·Windows 발급 정보 입력 화면 추가. 실제 키가 없어 실검색 미검증. 여러 사이트의 후보/미리보기, 제공된 파일 링크 보존. 각 원본 사이트의 파일 자동 보관은 미구현 |
| Flickr | 공개 피드 HTTP 응답 확인, 현재 robots에서 앱 경로 차단 | 기존 공개 피드는 사용 가능으로 세지 않음. 정식 API는 발급 정보/이용 조건을 별도로 조사·연결해야 함 |
| Bluesky | 공개 API robots 허용. 이 환경의 실제 검색 요청은 HTTP 403 | 지원 완료로 표시하지 않음 |
| Soompi | robots와 검색 주소 응답 200 | 실제 검색 사진 후보는 확인 못함. 연결 안 됨 |
| allkpop | robots·사이트·공식 검색 폼·q 검색 주소 응답 200 | 정적 HTML에서 검색 사진을 확인 못함. 내부 `/api/`는 robots 금지. 연결 안 됨 |
| Google Custom Search JSON API | 공식 문서에서 신규 고객 등록 종료, 기존 고객은 2027-01-01까지 전환 안내 확인 | 신규 사용자의 기본 연결로 추천하지 않음 |
| Brave Search 이미지 API | 인증 필요 후보. 이 환경의 API 호스트 robots 요청은 403 | 발급 정보·계약·실응답 검증 전 지원으로 세지 않음 |
| X·Instagram·Pinterest·블로그/언론 개별 사이트 | 이번 실제 검색 연동 검증 없음 | 계정/공식 API 범위·피드·페이지 구조를 출처별로 검증해야 함. 연결 완료 목록에 넣지 않음 |

## API 연결 사용

1. [네이버 애플리케이션 등록](https://developers.naver.com/apps/#/register)에서 앱을 등록하고 사용 API에 **검색**을 선택한다.
2. 앱의 **검색 출처 → 네이버 API 발급 정보 입력**에서 Client ID와 Client Secret을 입력한다. 채팅이나 보고서에 넣지 않는다.
3. **네이버 공식 이미지 검색 API**와 **DBKpop 공개 사진 검색**을 선택해 저장한다. Flickr는 현재 차단 상태이므로 꺼둔다.
4. 새로 검색하고 앱을 닫은 뒤 통합 검사 보고서를 확인한다. 실제 Naver 키 검증과 Windows 디코딩은 이 결과로 확인해야 한다.

Windows 발급 정보는 현재 계정의 DPAPI로 암호화해 앱 데이터의 `credentials/naver.dpapi`에 저장한다. settings JSON이나 DB, 배포 ZIP, 검사 보고서에는 저장하지 않는다. 네이버 인증 헤더는 API 호스트에만 전달하며 다른 호스트로의 리다이렉트는 허용하지 않는다. 이미지 CDN에는 인증 헤더를 붙이지 않는다. 개발 환경에서는 NAVER_CLIENT_ID/NAVER_CLIENT_SECRET 환경 변수도 지원한다. 실제 Windows DPAPI 실행은 이 Linux 검사에서 건너뛰었다.

공식 API 호출은 API 문서와 인증 계약에 따른 요청이다. 일반 페이지 크롤러에 적용되는 robots 탐색 경로와 구분한다. 미리보기 CDN 파일과 DBKpop 페이지/이미지는 기존 robots·크기·디코딩·권한 검사를 사용한다. 검색에 나타난다는 사실은 원본 사이트에서의 보관·AI 사용 허가를 자동으로 의미하지 않는다.

## 근거

- [DBKpop 공개 API](https://dbkpop.com/wp-json/) · [robots](https://dbkpop.com/robots.txt) · `live-checks/dbkpop-search.json`
- [네이버 이미지 검색 문서](https://developers.naver.com/docs/serviceapi/search/image/image.md)
- [Bluesky 검색 API 문서](https://docs.bsky.app/docs/api/app-bsky-feed-search-posts) · [공개 API robots](https://public.api.bsky.app/robots.txt)
- [Soompi robots](https://www.soompi.com/robots.txt) · [allkpop robots](https://www.allkpop.com/robots.txt)
- [Google Custom Search JSON API 현황](https://developers.google.com/custom-search/v1/overview)

이 표는 연결 개수를 부풀리기 위한 목록이 아니다. 지금 실제 추가 사진 디코딩까지 확인한 출처는 DBKpop이며, 네이버는 발급 정보 입력 후 실검색 검증이 남아 있다. 광범위한 원본 자동 보관과 1단계 전체 완료는 계속 미완료다.
