# SDOCX 실제 샘플 구조 조사

조사일: 2026-10-13 · 실행 환경: WSL / Python 3.12 · 범위: 로컬 구조 추출과 참고 코드 조사

> 이 문서는 이전 인용 추출 계획을 기준으로 수행한 연구 기록이다. 아래 실측 결과는 보존하지만, 인용 품질 관문·Markdown 갱신·자동 백업 도구 연동에 관한 제안은 현재 계획을 규정하지 않는다. 현재 요구사항은 [필기 포함 PDF 변환 계획](01_plan.md)을 따른다. 특히 메타데이터 파싱 실패, 도형 누락, PDF 없는 페이지, 회전 좌표 관측은 새 계획에도 관련된다.

관련 문서: [개발 계획](01_plan.md) · [검증 계획](03_verification.md) · [재현 방법 및 결과 파일 설명](../research/README.md)

## 1. 결론과 적용 범위

실제 `docs/data/`의 세 `.sdocx`에서 내장 PDF 3개, PDF 68페이지, 필기 2,322획을 회수했다. PDF는 모두 열리며 모든 페이지에서 텍스트가 추출된다. 노트 페이지의 명시적 PDF 참조도 읽을 수 있어 인용 추출 연구를 계속할 근거가 확보되었다.

그러나 Dietrich를 그대로 운영 파서로 채택할 수는 없다. 세 샘플 모두 `note.note` 파싱이 실패하고, MSD의 도형 객체 8개가 중간 모델에서 경고 없이 빠진다. 노트 UUID로 기대하는 필드는 비어 있다. PSS는 중간에 PDF 배경이 없는 페이지가 있고 PDF가 90도 회전되어 있다. 이 차이를 처리하지 않으면 노트 병합, 표시 누락, 출처 좌표 오류가 생긴다.

이번 결과는 **해당 세 파일의 관측 결과**다. 삼성노트 화면 캡처, 가져오기 전 PDF, 변경 전후 노트 쌍, 사람이 라벨링한 최소 50개 표시가 없으므로 인용 정확도·원본 동일성·ID 안정성·렌더링 충실도 관문은 통과로 기록하지 않는다.

## 2. 참고 저장소 조사

README뿐 아니라 아래 진입점/파싱/출력 코드를 읽고, Dietrich와 sdocx_parser는 실제 샘플에 실행했다. 아래 링크는 조사 커밋에 고정되어 있다.

| 저장소 / 커밋 | 실제 확인한 역할 | 이번 사용과 제한 |
| --- | --- | --- |
| [Dietrich](https://github.com/Downhillghost/Dietrich/tree/bb9c9202a8ab2bf96347e6cd0b16eaa354b80483) · `bb9c9202` | `SamsungNotesImporter.import_path()` → `SpenNotesPageParser` → `NoteDocument`; Kaitai 생성 파서와 PNG 렌더러 | 실제 구조 추출/PNG에 사용. MIT. 메타데이터 실패와 도형 누락을 별도 검출해야 함 |
| [sdocx_parser](https://github.com/0xA01F3/sdocx_parser/tree/49fae0968f1c690ae6e93d84a48d341518f69cd3) · `49fae096` | `parse_sdocx()`, `parse_note_title_body()`, `parse_pages()`가 텍스트와 객체 census 제공 | 실제 보조 비교 실행. GPLv3. 필기/이미지 렌더러가 아니며, `--pdf`는 HTML 재조판 출력이므로 내장 원본 PDF 회수와 다름 |
| [snotebackup](https://github.com/enyx24/snotebackup/tree/ae74a8a14ddd93ca2194dc75d74298d3b0809095) · `ae74a8a1` | Windows SQLite/노트 폴더 접근, `compress_to_sdocx()`, UUID 기반 증분 백업과 삭제 상태 관리 | 소스만 조사. Windows 앱/동기화/서비스 미실행. 체크아웃에 라이선스 파일 없음 |
| [Windows Markdown Exporter](https://github.com/joshua1955/Windows-Samsung-Notes-Markdown-Exporter/tree/0e24669445160a1b7ef662259cc7193cd31df92d) · `0e246694` | `export_notes.py`가 `Storage.sqlite`의 텍스트와 `wdoc` 이미지/thumbnail을 Markdown으로 출력 | 소스만 조사. SDOCX 페이지/획 좌표 인용 파서로 사용하지 않음. 체크아웃에 라이선스 파일 없음 |

라이선스 근거: [Dietrich LICENSE](https://github.com/Downhillghost/Dietrich/blob/bb9c9202a8ab2bf96347e6cd0b16eaa354b80483/LICENSE), [sdocx_parser LICENSE](https://github.com/0xA01F3/sdocx_parser/blob/49fae0968f1c690ae6e93d84a48d341518f69cd3/LICENSE). 라이선스 파일이 없는 두 저장소는 복사·배포 허가가 확인된 것으로 취급하지 않는다. GPL 보조 파서의 운영 통합/배포 방식은 별도 판단 대상으로 남긴다. 이번에는 원본 저장소를 로컬에 보관하고 실행/비교했으며 소스를 프로젝트 구현으로 복사하지 않았다.

참고로 snotebackup README는 백업 index를 JSON으로 설명하지만 현재 `incremental_service.py`의 기본 index는 `.snotebackup_index.sqlite`다. 연동은 README 설명만 믿지 말고 실제 해당 커밋 API/저장 형식에 맞춰야 한다.

## 3. 샘플과 실측 결과

약칭은 기존 파일명 구분에만 사용한다. 별도 노트 식별자로 사용하지 않는다.

| 항목 | JSSC | MSD | PSS |
| --- | ---: | ---: | ---: |
| 원본 파일 | `JSSC-SI-26-0608_Proof_hi_TriplerReview_260917_104914.sdocx` | `MSD-03_260929_154931.sdocx` | `PSS_Validation_Discussion_Deck_260930_150529.sdocx` |
| SDOCX 크기(bytes) | 21,888,363 | 3,734,678 | 3,094,529 |
| ZIP member 수 | 31 | 74 | 23 |
| 원시 `.page` 수 | 25 | 38 | 9 |
| Dietrich 모델 페이지 수 | 24 | 37 | 8 |
| 내장 PDF 페이지 수 | 24 | 37 | 7 |
| PDF 크기(bytes) | 21,851,908 | 1,332,603 | 1,130,317 |
| PDF 텍스트가 추출된 페이지 | 24 | 37 | 7 |
| 표준 PDF annotation 수 | 0 | 0 | 0 |
| `.page` 필기 획 수(type 1) | 5 | 1,738 | 579 |
| 필기 점 수 | 1,737 | 278,536 | 97,417 |
| 이미지 객체 수(type 3) | 0 | 0 | 2 |
| 도형 객체 수(type 7) | 0 | 8 | 0 |
| 페이지 텍스트 필드 수(type 2) | 0 | 0 | 0 |
| 본문 비공백 문자 수 | 0 | 0 | 0 |
| `.spi` 수 | 1 | 31 | 7 |

PSS 본문은 줄바꿈 문자 80개다. 이를 실제 메모 80자나 누락된 문장으로 해석하면 안 된다. 제목 문자열은 보조 파서로 세 파일 모두 회수했지만, 제목은 노트 UUID가 아니다.

모든 원본의 실행 전후 SHA-256은 동일했다. 회수한 PDF 3개는 ZIP 안의 해당 member와 바이트 비교가 일치하고 PyMuPDF가 복구 없이 열었다. 이것은 **내장 자산을 정확하게 복사했다**는 뜻이다. 삼성노트에 가져오기 전 PDF와 바이트가 같은지는 비교 원본이 없어 미확인이다.

실측 근거: [전체 요약](../research/output/2026-10-13/summary.json), [진단 집계](../research/output/2026-10-13/diagnostics.json), [환경/버전](../research/output/2026-10-13/environment.json). 샘플에서 파생된 전체 산출물은 Git 관리에서 제외했다.

## 4. 실제 컨테이너 구조와 필드 연결

```text
sample.sdocx                         ZIP
├── note.note                        노트 헤더, 제목/본문 객체, 선택 메타데이터
├── pageIdInfo.dat                   페이지 UUID의 순서
├── <page_uuid>.page                 페이지 크기/배경/레이어/객체/획 좌표
├── end_tag.bin                      요약 sidecar
└── media/
    ├── mediaInfo.dat                bind_id → 파일명
    ├── 0@pdf_….pdf                  실제 PDF 자산
    ├── <bind_id>@page_….spi          필기/페이지 cache 계열 바이너리
    └── <bind_id>@files_….png         PSS의 삽입 이미지 2개
```

ZIP의 순서나 파일명 정렬을 페이지 순서로 사용하지 않는다. `pageIdInfo.dat.entries[].page_id` 순서와 `.page` 내부 UUID 및 보조 파서 순서는 세 샘플 모두 일치했다. 페이지 UUID는 읽혔지만 반복 저장에서 유지되는지는 아직 미검증이다.

PDF 참조 흐름은 다음과 같다.

```text
pageIdInfo.dat → page UUID → <UUID>.page
  metadata.pdf_data_list[]
    file_id ─────→ mediaInfo.dat의 bind_id → 실제 PDF 파일
    page_index ──→ 그 PDF 안의 0-based 페이지
    rect ────────→ 노트 좌표계에서 PDF 배경이 놓인 영역
```

세 파일의 `.page` 포맷 버전은 모두 `4000`이며 `min_format_version`도 4000이다. 이번 샘플에서 읽은 주요 바이너리 위치는 다음과 같다. 다른 버전 전체에 대한 새 규격으로 일반화하지 않는다.

| `.page` 위치/조건 | 의미 | 이번 관측 |
| --- | --- | --- |
| `0x00`, u32 LE | 레이어 offset | 이번 파일은 header offset을 사용 |
| `0x04`, u32 LE | property offset | 128 |
| `0x0E`, u32 LE | property mask | PDF/cache/배경 필드의 존재를 결정 |
| `0x16`, `0x1A`, u32 LE | 페이지 폭/높이 | PDF 페이지/노트마다 다름 |
| `0x26`, u16 LE 및 `0x28` 이후 | UUID 길이와 UTF-16LE 문자열 | 길이 36, 파일명 UUID와 일치 |
| property mask `0x100` | PDF data list | `file_id`, `page_index`, `rect` |
| property mask `0x400` | canvas cache list | `.spi` bind ID 등 |

필드 상세의 기반은 Dietrich의 [page 명세](https://github.com/Downhillghost/Dietrich/blob/bb9c9202a8ab2bf96347e6cd0b16eaa354b80483/sdocx_specification/page_format_specification.md)와 [실행 파서](https://github.com/Downhillghost/Dietrich/blob/bb9c9202a8ab2bf96347e6cd0b16eaa354b80483/note_pipeline/input/samsung_notes/page_parser.py)다. 이번 조사는 그 해석을 실제 세 샘플에 적용한 결과를 `page_records.json`에 보존했다.

## 5. PDF와 노트 페이지는 다른 단위

아래는 PSS의 실제 대응이며, 표시 번호는 모두 1-based다.

| 노트 페이지 | PDF 페이지 | 해석 |
| ---: | ---: | --- |
| 1 | 1 | PDF 배경 + 삽입 PNG + 필기 |
| 2 | 없음 | 삽입 PNG 1개 + 필기 158획. 원본 PDF 페이지를 임의로 부여하면 안 됨 |
| 3 | 2 | 명시적 참조 사용 |
| 4 | 3 | 명시적 참조 사용 |
| 5 | 4 | 명시적 참조 사용 |
| 6 | 5 | 명시적 참조 사용 |
| 7 | 6 | 명시적 참조 사용 |
| 8 | 7 | 명시적 참조 사용 |
| 9 | 없음 | 이번 파일에서는 객체/배경 없는 마지막 페이지 |

따라서 `note_index == pdf_index` 가정은 PSS의 두 번째 페이지부터 깨진다. 이 관측만으로 사용자가 언제 페이지를 추가했는지까지 확정하지는 않는다.

세 샘플은 모두 객체/배경 없는 마지막 페이지가 하나씩 있고 Dietrich가 이를 모델에서 제거했다. 연구 결과에는 원시 페이지와 제거 여부를 함께 남겼다. [upstream의 제거 조건](https://github.com/Downhillghost/Dietrich/blob/bb9c9202a8ab2bf96347e6cd0b16eaa354b80483/note_pipeline/input/samsung_notes/package.py)은 단순히 “객체가 하나도 없는가”보다 느슨해서 짧은 획이나 일부 객체를 놓칠 수 있다. 운영 구현에서 이 규칙을 그대로 삭제 정책으로 쓰면 안 된다.

## 6. 좌표와 representation

필기는 `points: [(x, y), ...]`와 색, `pen_size`, layer, 압력, timestamp 등으로 나온다. 총 377,690개 점을 얻었으며 각 획의 압력/timestamp 배열 길이는 점 수와 모두 같았다. timestamp 단위/시간 기준의 의미는 이번 조사에서 검증하지 않았다.

PDF 단어는 `get_text("words")`의 bbox와 block/line/word 번호로 별도 추출했다. 이 원문 텍스트와 노트 텍스트 객체를 섞지 않는다. PDF 배경 크기를 고정 상수로 두면 안 된다. 예를 들어 MSD 배경 rect는 1848×1386 또는 2960×2220으로 달라진다.

PSS PDF 7페이지는 모두 rotation=90이다. PyMuPDF가 반환한 비회전 텍스트 bbox를 다음 순서로 노트 좌표에 옮겼다.

```python
display_bbox = pdf_bbox * pdf_page.rotation_matrix
note_bbox = display_bbox * pdf_page.rect.torect(background_rect)
```

회전이 없는 경우에는 PDF 표시 폭/높이에 대한 비율로 배경 rect의 크기와 원점에 맞추는 변환이다. 회전 후 좌표와 배경 rect가 핵심이며, 무조건 DPI=72로 나누는 방식이 아니다. [PyMuPDF 좌표 문서](https://pymupdf.readthedocs.io/en/latest/page.html)와 실제 PDF rotation 필드를 함께 참고했다.

`pdf_words_in_note.json`에는 변환 전 bbox, rotation matrix, fit matrix, 변환 후 bbox를 저장했다. 이는 **표시와 원문을 비교하기 위한 좌표 자료**이고 자동 인용문이 아니다. 글자별 경계가 필요하면 다음 실험에서 `rawdict`의 char bbox를 추가하는 것이 적절하다. 회전/크롭/페이지 재배열의 모든 변형을 검증한 것은 아니다.

PSS PDF 2페이지의 50개 단어 bbox를 [진단 그림](../research/output/2026-10-13/PSS_Validation_Discussion_Deck_260930_150529/coordinate_check.png)에 겹쳐 실제 글자 위치와 맞는 것을 육안 확인했다. 이는 해당 PDF의 회전 좌표 변환 확인이며 삼성노트 기준 화면과의 비교를 대체하지 않는다.

## 7. 실제 발견한 파서 제한

### 7.1 note.note 메타데이터 실패

세 샘플에서 동일하게 `SamsungNote.StringIdBlock._read()`의 entry count 읽기가 실패했다.

```text
EndOfStreamError: requested 2 bytes, but only 0 bytes available
```

관측값:

- `note.note` 헤더의 format version=4000, property flags=`0xC8E80`.
- 파서가 해석한 `string_id_block_size=0`.
- 실패 시 바깥 스트림 위치는 JSSC=837, MSD=736, PSS=2861.
- 그 전에 읽힌 `last_edited_page_index=1848`은 실제 페이지 수보다 커서 정상 의미로 해석하기 어렵다.

**확정 사실:** 실패 위치는 string ID block이고, `load_samsung_note_source()`는 이를 오류 문자열로 돌려준다. importer는 비어 있는 메타데이터로 계속 진행한다. 따라서 페이지/PNG 성공만 보면 메타데이터 실패를 놓친다.

**미확정 원인:** 4000 포맷의 선택 필드 배치 또는 해당 파서의 offset 해석이 맞지 않을 가능성이 있다. 앞 필드가 이미 어긋난 징후가 있으므로 “길이 0인 block만 허용”하는 수정으로 해결됐다고 판단하면 안 된다. 원본 파일 손상으로 확정할 근거도 없다.

### 7.2 노트 정체성

`note.note`의 규격상 note ID 문자열 길이 필드(`0x12`, u16 LE)는 세 파일 모두 0이다. `end_tag.bin` 파서의 `note_id`도 빈 문자열이다. 이번 입력에서 신뢰할 만한 노트 UUID는 확보하지 못했다. 페이지 UUID나 제목/본문 객체의 UUID를 노트 UUID로 승격하지 않는다.

Dietrich는 note ID가 없으면 display name으로 대체한다. 이번에는 영구 추출 폴더 `raw/`를 입력했기 때문에 세 모델의 `note_id`가 모두 `raw`가 되었다. 이는 **실제 동일 노트라는 뜻이 전혀 아니다**. 운영 식별에 사용하지 말고 계획의 “ID 불명 → 격리/수동 연결” 정책을 적용해야 한다. 자동 백업 경로를 도입할 때 Windows DB UUID와의 대응을 별도 검증한다.

Dietrich의 asset ID 생성에는 `source_path`도 들어간다. 추출 위치를 바꾸면 달라질 수 있으므로 이 값도 장기 식별자로 바로 저장하지 않는다.

### 7.3 도형 8개가 중간 모델에서 누락

MSD `.page` 원시 레이어와 보조 파서 양쪽에서 type 7 객체 8개를 확인했다. 노트 페이지 2에 2개, 3·4·5·21·22·24에 각각 1개다. 보조 파서는 이를 `Shape`로 분류한다. 구체적인 도형 종류와 시각적 의미는 아직 해석하지 않았다.

Dietrich 모델에는 이 8개가 없고 `UnsupportedElement`도 0개다. importer가 처리하는 페이지 property의 `custom_objects`와 일반 레이어의 type 7은 다른 경로다. 따라서 현재 모델의 unsupported 개수만 세면 누락을 놓친다. `diagnostics.json.objects_without_model_support`에 페이지 UUID와 원시 바이트 offset/크기를 남겼다.

향후 어댑터에는 **원시 객체 census와 출력 모델 객체의 대응 확인**이 필요하다. 지금 단계에서 누락 도형을 단순 필기로 대체하거나 완전한 렌더링으로 표시하지 않는다.

### 7.4 표시 분류와 텍스트 범위

세 PDF의 표준 annotation은 0개다. PDF 페이지 content stream에서 조사한 `SPenSDK*` overlay tag도 0회였지만, PDF 내부 모든 중첩 XObject를 검사한 것은 아니다. 이 결과는 노트 표시가 없다는 뜻이 아니다. 실제 표시/필기는 `.page`의 획으로 관찰된다.

JSSC 미리보기에서 밑줄과 동그라미가 함께 보인다. 둘 다 일반 stroke 객체이므로 색이나 bbox만으로 모두 인용 처리하면 문서 정책을 위반한다. 또한 note 메타데이터 파싱 실패로 `pen_name`은 전부 미해결이고 정수 `pen_name_id`만 남았다. 선택형 하이라이트/펜 형광펜 유형 분리는 이번 샘플의 객체 수만으로 확정할 수 없다.

타이핑/변환 손필기 문장은 확인되지 않았다. 별도 텍스트 샘플 없이 텍스트 보존을 통과로 처리하지 않는다.

### 7.5 SPI와 렌더링

`.spi` 39개는 Dietrich가 메타데이터와 원본 자산을 보존하지만 불투명 payload를 픽셀로 디코딩하지 않는다. 참고: [SPI 명세](https://github.com/Downhillghost/Dietrich/blob/bb9c9202a8ab2bf96347e6cd0b16eaa354b80483/sdocx_specification/spi_format_specification.md).

미리보기 5장(JSSC 1장, MSD/PSS 각 2장)을 실제 생성했다. JSSC 3페이지, MSD 2페이지, PSS 2페이지 이미지를 열어 PDF/삽입 이미지/필기 배치가 출력됨을 확인했다. 특히 PSS 2페이지는 PDF가 아닌 삽입 이미지 위의 필기다. 삼성노트 기준 화면과 비교하지 않았고 MSD 도형 누락도 있으므로 화면 재현 품질은 미검증이다.

## 8. 결과 재사용 방법과 다음 연구

빠른 접근:

- [JSSC 페이지 매핑](../research/output/2026-10-13/JSSC-SI-26-0608_Proof_hi_TriplerReview_260917_104914/page_map.csv)
- [MSD 페이지 매핑](../research/output/2026-10-13/MSD-03_260929_154931/page_map.csv)
- [PSS 페이지 매핑](../research/output/2026-10-13/PSS_Validation_Discussion_Deck_260930_150529/page_map.csv)
- [JSSC 미리보기](../research/output/2026-10-13/JSSC-SI-26-0608_Proof_hi_TriplerReview_260917_104914/preview_note_page_003.png)
- [PSS PDF 없는 페이지 미리보기](../research/output/2026-10-13/PSS_Validation_Discussion_Deck_260930_150529/preview_note_page_002.png)

원시 파서 모델을 그대로 운영 모델로 확정하지 않고, 아래 항목을 후속 어댑터의 입력 근거로 사용한다.

| 향후 필요한 값 | 현재 근거 | 남은 작업 |
| --- | --- | --- |
| 노트 UUID | 확보 실패 | DB/백업 UUID 연결 또는 격리 정책 |
| 노트 제목/텍스트 | 보조 파서 title/body | Dietrich 메타데이터/본문 경로 수정, 실제 텍스트 샘플 검증 |
| 페이지 key/순서 | page UUID + pageIdInfo | 변경 전후 안정성 확인 |
| PDF 페이지 출처 | `file_id`, `page_index`, `rect` | PDF 없는 중간 페이지 지원 상태 표시 |
| 표시 geometry | stroke points/pressure/style | 밑줄·형광펜·동그라미 구분, pen string map 회수 |
| 읽을 원문 | PDF words + 변환된 bbox | 글자/행 범위 대응 실험, 사람이 확인한 정답 |
| 미지원 객체 | 원시 census와 모델 비교 | MSD type 7 보존/렌더링 경로 |
| 필기 미리보기 | 생성된 5개 PNG | 기준 화면 비교와 누락 복구 |

권장 다음 순서는 (1) 4000 버전 note 선택 필드 파싱 원인 규명, (2) type 7 누락을 명시적으로 드러내는 어댑터, (3) JSSC의 실제 밑줄을 이용한 원문 좌표 대응 실험, (4) 선택형 표시와 타이핑/변환 텍스트 샘플 및 기준 화면 추가다. 자동화/사용자 Markdown 갱신 개발은 기존 품질 관문 이후에 진행한다.

## 9. 현재 검증 판정

| 항목 | 이번 상태 |
| --- | --- |
| V01 Windows 동기화/백업 | 미실행 |
| V02 내장 PDF 회수 | 세 파일에서 바이트 회수·열기·페이지 순회 성공. 가져오기 전 원본 대비 누락 여부는 미확인 |
| V03 원본 동일성 | ZIP member와 일치. 가져오기 전 PDF와 비교 미실행 |
| V04 페이지 연결 | 68개 PDF 배경의 참조를 해석. PSS 중간 1페이지는 PDF 출처 없음 |
| V05 ID 안정성 | 페이지 ID는 관측. 노트 UUID 확보 실패, 변경 전후 비교 미실행 |
| V06 표시/좌표 | 획과 단어 좌표 추출. 유형 분리와 인용 범위 정확도 미검증 |
| V07 텍스트 분리 | PDF 텍스트는 추출. 실제 사용자 텍스트를 검증할 샘플 부족 |
| V08 필기 미리보기 | 5장 생성. 삼성노트 기준 비교 미실행, MSD 도형 누락 확인 |
| Gate 1 이상 | 모두 미실행 |

이번 작업은 샘플 구조 연구를 수행한 결과이며 Gate 0 전체 통과나 MVP 구현 완료를 의미하지 않는다.
