# 현재 연구: 자체 분석기

새 실행 경로는 [standalone/README.md](standalone/README.md)다. Python 표준 라이브러리만 사용하며 아래 설치 절차는 필요 없다. 결과는 [자체 포맷 연구](../docs/06_standalone_format_research.md)를 따른다.

---

# 과거 외부 파서 연구 (보존)

# SDOCX 구조 연구 재현

결과 해석은 [조사 문서](../docs/05_structure_research.md)를 먼저 읽는다. 이 디렉터리는 연구 도구이며 Vault 갱신 프로그램이 아니다.

## 파일

- `inspect_samples.py`: ZIP 내부 파일을 그대로 회수하고 Dietrich/보조 파서 모델, PDF 텍스트와 좌표, 일부 페이지 PNG를 생성한다.
- `analyze_structure.py`: 첫 단계 결과에서 페이지 매핑 CSV, 누락 객체/메타데이터 오류 진단, PDF 단어의 노트 좌표를 만든다.
- `requirements.txt`: 실제 실행한 연구 환경 패키지 버전. 프로젝트 전체의 운영 의존성으로 확정한 것은 아니다.
- `upstream_versions.json`: 조사한 네 저장소의 정확한 커밋.
- `upstream/`: 원본 참고 저장소. 수정하지 않았으며 Git 관리 제외.
- `output/2026-10-13/`: 이번 최종 결과. 원문과 필기가 있으므로 Git 관리 제외.
- `inspection.log`: 최종 첫 단계 stdout/stderr. `output/initial/`은 최초 직렬화 실패 흔적, `output/structure/`는 중간 성공 실행이다. 최종 결과와 섞어 사용하지 않는다.

## 환경 준비

저장소 루트 `/home/thkim0/github/SNo2md`에서 실행한다. 기존 `research/.venv`와 `research/upstream`이 있으면 설치/clone을 반복할 필요가 없다.

```bash
python3 -m venv research/.venv
research/.venv/bin/pip install -r research/requirements.txt
git clone https://github.com/Downhillghost/Dietrich.git research/upstream/Dietrich
git clone https://github.com/0xA01F3/sdocx_parser.git research/upstream/sdocx_parser
git clone https://github.com/enyx24/snotebackup.git research/upstream/snotebackup
git clone https://github.com/joshua1955/Windows-Samsung-Notes-Markdown-Exporter.git research/upstream/Windows-Samsung-Notes-Markdown-Exporter
```

새 clone은 최신 커밋일 수 있다. 동일 결과를 재현하려면 각 참고 저장소를 `upstream_versions.json`에 기록한 커밋으로 맞춘다. 예를 들어 Dietrich의 경우:

```bash
git -C research/upstream/Dietrich checkout --detach bb9c9202a8ab2bf96347e6cd0b16eaa354b80483
```

나머지 저장소도 동일하게 기록된 커밋을 사용한다. 실제 파싱에 import하는 저장소는 Dietrich와 sdocx_parser이고, 나머지 둘은 소스 조사용이다. 각 저장소의 라이선스 파일은 원본 체크아웃에 보존되어 있다. GPLv3 보조 파서를 배포 코드에 통합하는 결정은 이번 연구 범위에 포함하지 않는다.

## 실행

```bash
research/.venv/bin/python research/inspect_samples.py \
  --input docs/data \
  --output research/output/new-run
research/.venv/bin/python research/analyze_structure.py research/output/new-run
```

첫 단계는 기존 출력 디렉터리를 덮어쓰지 않는다. 재실행에는 새 경로를 지정한다. 둘째 단계는 해당 실행 폴더의 파생 진단 파일만 다시 쓴다. 기본 PNG 생성 수는 샘플별 필기가 있는 첫 2페이지이며 `--render-count 0`으로 생략할 수 있다. 모든 페이지의 원시 구조와 모델은 PNG 생성 수와 관계없이 추출한다.

스크립트는 샘플을 외부로 보내지 않는다. 원본은 읽기만 하며, ZIP CRC 확인과 실행 전후 SHA-256 비교를 수행한다. 이 해시는 연구 결과와 입력을 대응시키기 위한 것이며 노트 UUID 대체값이 아니다.

## 결과 읽기

각 샘플은 원래 파일명에서 확장자만 뺀 디렉터리에 저장된다.

| 파일 | 내용 |
| --- | --- |
| `raw/` | 원래 ZIP member를 바이트 그대로 회수. PDF도 `raw/media/`에 있음 |
| `archive_inventory.json` | 내부 경로, 크기, 압축 크기, CRC, 처음 32바이트 |
| `sidecars.json` | 페이지 순서, 미디어 bind ID, end tag |
| `note_metadata.json` | Dietrich의 note 메타데이터 결과와 파싱 오류 |
| `page_records.json` | 모든 원시 페이지의 속성, PDF 배경, 획, 이미지, 텍스트 필드, 레이어/객체 |
| `neutral_model.json` | Dietrich 중간 모델. 누락이 있으므로 완전한 정답 모델로 사용 금지 |
| `sdocx_parser_model.json` | 보조 파서의 제목/본문/페이지 텍스트/객체 census |
| `pdf_inventory.json` | 내장 PDF별 모든 페이지 텍스트, 단어 bbox, 회전, 표준 PDF annotation |
| `page_map.csv` | 사람이 읽는 1-based 노트/PDF 페이지 대응과 객체 수 |
| `pdf_words_in_note.json` | 회전을 적용한 PDF 단어 bbox의 노트 좌표. 인용 결과가 아님 |
| `stroke_inventory.json` | 획별 bbox, 점 수, 압력/시간 개수, 색과 굵기 |
| `diagnostics.json` | 메타데이터 실패 traceback, 도형 누락 위치, 파일/순서 비교 결과 |
| `preview_note_page_NNN.png` | Dietrich가 그린 일부 페이지. 삼성노트 기준 화면과 비교 전 |
| `coordinate_check.png` | 회전된 PDF가 있는 샘플의 단어 bbox 진단 그림 |
| `summary.json` | 해당 샘플 요약 |

실행 루트의 `summary.json`, `diagnostics.json`은 샘플별 결과를 모으고 `environment.json`은 Python/OS/패키지/참고 커밋을 기록한다.

JSON의 `_type`은 dataclass/Kaitai 클래스 이름이고, 바이너리는 `{encoding: "hex", data: "..."}`로 보존한다. JSON의 페이지 `index`는 0-based이며 CSV의 `*_1based`만 1-based다. 원본 파서가 가진 절대 로컬 경로는 그대로 남겨 두었으므로 폴더를 옮기면 재실행한다. `page_map.csv`의 `pdf_member`는 `raw/` 기준 상대 경로다.

진단은 고정 커밋의 비공개 `_parse_layers()` API도 사용한다. upstream을 바꿀 때 이 호출과 타입별 객체 수를 다시 확인한다. 새 포맷의 파싱 실패가 생기면 `errors`, `note_parse_error`, `diagnostics.json`을 먼저 읽고 빈 결과를 성공으로 간주하지 않는다.
