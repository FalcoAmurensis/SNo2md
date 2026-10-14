# 전체 SDOCX → PDF 변환 구현

2026-10-14 · 사용자 선택: PDF 합성에 pypdf 사용 · WSL에서 실제 샘플 변환 실행

## 구현

[convert_sdocx.py](../research/standalone/convert_sdocx.py)는 SDOCX/압축 해제 폴더를 직접 받아 전체 노트를 PDF로 저장한다. 중간 `structure.json`이나 외부 파서가 필요 없다. 설치와 실행은 [루트 README](../README.md)를 따른다.

1. 기존 자체 `load_input`, `sidecars`, `page`, `stroke` 해독을 재사용한다.
2. `note.note` 제목/본문을 객체 내부 TextCommon 오프셋으로 읽는다. 제목은 PDF 메타데이터에 넣고, 아직 렌더링할 수 없는 비공백 본문은 오류로 처리한다.
3. `pageIdInfo.dat` 순서대로 출력 페이지를 만든다. 페이지 크기는 노트 좌표값을 PDF 단위로 사용하므로 원본 PDF의 인쇄 용지 크기와 같다는 보장은 없다.
4. 배경 PDF의 회전을 콘텐츠에 적용하고 cropbox를 노트의 PDF 사각형에 맞춰 변환·합성한다. 원본 콘텐츠 스트림과 폰트 리소스를 사용해 검색·선택 가능한 텍스트를 유지하는 구조다. 화면 캡처를 PDF로 바꾸지 않는다.
5. 필기 좌표는 `m/l/S`, 둥근 선 끝과 꺾임, ARGB의 alpha 상태로 직접 PDF 경로를 작성한다. SVG 변환기나 이미지 렌더러를 호출하지 않는다.
6. 지원 도형은 경로와 스타일을 자체 해독하며 quadratic segment는 동일한 곡선의 cubic PDF 명령으로 바꾼다. 삽입 PNG는 표준 라이브러리로 scanline filter를 복원하고 RGB 이미지와 alpha soft mask로 넣는다.
7. 모든 페이지가 성공한 뒤 같은 출력 폴더에 임시 파일을 완성하고 새 경로에 게시한다. 기존 경로는 거부하며 원본 입력은 읽기만 한다.

공식 pypdf의 [페이지 합성](https://pypdf.readthedocs.io/en/stable/user/add-watermark.html)과 [회전/콘텐츠 병합 API](https://pypdf.readthedocs.io/en/stable/modules/PageObject.html)를 참고했다. `pypdf==6.20.1`로 고정했고 이미지/스트림을 간접 객체로 등록할 때 `_add_object`를 사용하므로 버전 변경 시 해당 부분을 확인해야 한다.

## 생성 결과

| 파일 | 노트 페이지 | PDF 배경 | 필기 | 도형 | 이미지 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `research/output/converted/JSSC.pdf` | 25 | 24 | 5 | 0 | 0 |
| `research/output/converted/MSD.pdf` | 38 | 37 | 1,738 | 8 | 0 |
| `research/output/converted/PSS.pdf` | 9 | 7 | 579 | 0 | 2 |

숫자는 실제 변환 실행 중 처리한 페이지/객체 개수다. 출력 PDF를 다시 분석한 검증 통계는 아니다. 각 노트 끝의 빈 페이지도 보존했다. 내부 placeholder를 확실한 규칙 없이 제거하지 않는다.

이번에는 변환 실행으로 세 PDF를 생성했으며 자동 테스트, 전체 페이지의 육안 대조, 최종 출력의 텍스트 추출 비교, Windows/Obsidian 실행 검증은 수행하지 않았다. 한 페이지의 기존 근사 SVG 비교는 앞선 연구이며 이번 전체 PDF 검증을 대신하지 않는다.

## 새로 해독한 샘플 프로필

외부 SDOCX 구현 코드를 복사하지 않고 실제 `.page` 바이트와 기존 포맷 설명을 대조했다. 다음은 **관측한 프로필에 한정한 구현**이다.

- Shape subrecord의 compact mask 다음에 shape kind, 4개의 float64 경계, 예약값, 길이가 있는 path가 온다. path는 u32 명령 수와 u8 opcode/좌표 배열이다.
- 관측 opcode 1/2/3/6을 각각 move/line/quadratic/close로 해석한다. 다른 opcode는 실패한다.
- MSD 객체 type 7의 shape kind는 1, 4, 88이었다. 타입 번호를 일반 도형 이름으로 단정하지 않고 저장된 path를 사용한다.
- MSD shape-base own block은 39바이트 프로필이며 선 ARGB는 +7, float32 굵기는 +27이었다. 프로필의 고정 영역도 확인해 다른 스타일을 무조건 이 오프셋으로 읽지 않는다.
- PSS 이미지의 shape fill type 2에서 bind ID를 읽는다. axis-aligned 사각형 경로와 단순 image-own/layout 프로필만 허용한다. 크롭/회전 이미지에 같은 처리를 임의로 적용하지 않는다.
- 두 PNG는 non-interlaced 8-bit RGBA였다. PNG filter 0~4를 역변환하고 alpha를 PDF soft mask로 보존한다. 색상 관리 프로필의 정밀 재현은 제공하지 않는다.

JSSC 내장 PDF에서 `/Link` 주석 160개가 확인됐다. 과거 연구의 “표준 주석 0”은 링크까지 없다는 뜻으로 해석하면 안 된다. pypdf의 페이지 합성은 링크 주석도 복사/변환한다. 링크 이동 대상과 회전된 링크 위치의 완전한 보존은 이번에 별도 확인하지 않았다.

## 제한과 실패 정책

- 비압축 필기, 간접 참조 색상/굵기, 타이핑 본문/텍스트 객체, 그룹 객체, 미지의 도형 스타일, 이미지 크롭/회전, 배경 이미지/템플릿은 미지원 시 중단한다.
- PDF의 링크 이외의 주석은 아직 처리하지 않고 중단한다. 폼/첨부/문서 책갈피 등의 문서 수준 보존도 현재 계약에 포함하지 않는다.
- 실제 Windows 백업에서 노트 루트를 찾는 작업과 SQLite 연동은 포함하지 않는다. 한 노트의 파일이 완성된 상태로 주어져야 한다.
- 파일 감시/등록/동일 경로 자동 교체는 아직 없다. 지금 CLI는 새 PDF 생성만 제공한다.
- 메모리 내 전체 노트 처리 방식이며 큰 노트의 메모리 최적화는 하지 않았다.
- 근사 필기와 샘플 도형 경로를 제공하는 수준이며 삼성노트 펜의 압력/속도/재질을 재현하지 않는다.
