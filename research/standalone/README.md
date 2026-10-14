> 전체 PDF 변환은 [루트 README](../../README.md)를 따른다. `convert_sdocx.py`만 pypdf 의존성이 있으며, 아래 구조 분석기와 SVG 도구는 계속 표준 라이브러리만 사용한다.

# 자체 SDOCX 구조 분석기

Python 3.9 이상, **표준 라이브러리만** 필요하다. pip 설치, 외부 저장소, 기존 연구 결과 없이 실행한다. 한 번에 노트 하나를 받으며 입력은 SDOCX ZIP 또는 압축 해제된 노트 루트다.

```bash
python3 -I -S research/standalone/inspect_sdocx.py \
  docs/data/MSD-03_260929_154931.sdocx \
  research/output/my-independent-run

python3 -I -S research/standalone/inspect_sdocx.py \
  research/output/my-independent-run/raw \
  research/output/my-folder-run
```

Windows에서는 `python -I -S ...`로 같은 스크립트를 실행할 수 있도록 표준 라이브러리 경로 처리만 사용했다. 실제 Windows 실행은 아직 하지 않았다. `-I -S`는 사용자 Python 경로와 site-packages 로딩을 차단한다.

출력 폴더는 새 경로여야 한다. 실패한 실행의 부분 결과도 자동 삭제하거나 덮어쓰지 않는다.

- `raw/`: 입력 내부 파일 그대로. PDF/PNG/SPI와 미해석 영역의 원본을 함께 보존한다.
- `structure.json`: 페이지 순서, 미디어 표, PDF 배경 연결, 모든 객체/하위 레코드의 바이트 범위, 필기 좌표·압력·시간·기울기·방향, 노트 고정 헤더, 파일별 바이트 크기와 SHA-256.
- `summary.json`: 객체 종류별 개수, 복원한 획/점, 문제 목록, `render_ready: false`.

바이트 범위는 **해당 원본 파일 시작 기준**, `offset`부터 `length`바이트다. 페이지 인덱스는 0부터 시작한다. 원문과 필기가 포함되므로 결과 폴더를 공개하지 않는다.

이 도구는 연구용 구조 분석기다. 도형/이미지는 객체 프레임만 읽으며 실제 렌더링, PDF 텍스트 파싱·합성, 감시와 Vault 갱신은 제공하지 않는다. `issues: []`는 지원 경로에서 파싱 오류가 없었다는 뜻이며 완전한 변환을 의미하지 않는다. 알 수 없는 버전/깨진 프레임은 오류로 중단하며, 지원하지 않는 필기 형식은 해당 레코드와 오류를 남긴다. 모든 내부 무결성 해시의 의미를 검증하지는 않는다.

[관측 및 구현 근거](../../docs/06_standalone_format_research.md) · [출처 및 코드 작성 범위](PROVENANCE.md)

## 좌표 기반 근사 렌더링

`render_strokes_svg.py`는 자체 `structure.json`의 필기를 일정한 굵기의 SVG로 만든다. 표준 라이브러리만 사용하며 압력/속도 보정은 생략한다. 저장된 색상·펜 크기, 둥근 선 끝/꺾임을 사용한다. 지원하지 않는 객체가 있으면 누락시키지 않고 중단한다. 현재 샘플용이며 일반 객체 변환기는 아니다.

```bash
python3 -I -S research/standalone/render_strokes_svg.py \
  research/output/standalone-2026-10-14/jssc/structure.json \
  research/output/comparison-jssc-page003/strokes.svg --page 3
```

SVG 파일이 이미 있으면 덮어쓰지 않는다. 비교 그림의 재생성은 `research/.venv/bin/python research/make_page_comparison.py`로 수행하며, 고정된 JSSC 자료를 사용해 비교 PNG를 덮어쓴다. 이 **비교 그림 제작 도구**는 기존 PyMuPDF/Pillow 환경을 사용하므로 독립 실행 도구와 구분한다. Dietrich를 다시 실행하거나 import하지 않는다.
