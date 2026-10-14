# SNo2md — Samsung Notes → PDF

SDOCX 파일 또는 압축 해제된 노트 폴더를 필기 포함 PDF로 변환한다. SDOCX 해독과 필기/도형 경로 생성은 자체 코드이며 PDF 합성에는 `pypdf`만 사용한다. 현재는 단일 노트 전체 변환 CLI이고 Windows 폴더 감시와 Vault 자동 갱신은 아직 없다.

## 설치·실행

Python 3.9 이상. 프로젝트 루트에서:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python research/standalone/convert_sdocx.py input.sdocx output.pdf
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe research\standalone\convert_sdocx.py input.sdocx output.pdf
```

첫 번째 인수에 압축 해제된 **노트 하나의 루트 폴더**를 넣어도 된다. 출력은 입력 폴더 밖의 새 경로여야 한다. 이미 존재하는 파일을 덮어쓰는 옵션은 제공하지 않는다.

```bash
.venv/bin/python research/standalone/convert_sdocx.py unpacked-note output.pdf --width-scale 1.0
```

`--width-scale`은 필기 및 지원 도형의 선 굵기에 적용하는 배율이다. 기본 1.0은 비교 자료에서 사용한 근사 복원 방식이다.

## 현재 지원

- v4000 SDOCX의 페이지 순서·크기와 PDF 배경. 원본 PDF 콘텐츠/폰트를 벡터 합성하며 전체 페이지를 이미지로 바꾸지 않는다.
- 압축 필기의 좌표, 직접 색상, 기본 굵기. 압력·속도 변화는 반영하지 않는다.
- 현재 MSD 샘플에서 관측한 도형 프로필의 직선·2차 곡선·닫힌 경로와 선 스타일.
- 현재 PSS 샘플에서 관측한 일반 삽입 이미지 프로필. 비인터레이스 8-bit RGB/RGBA PNG와 알파 채널.
- PDF 배경 없는 페이지와 끝의 빈 페이지를 포함한 **모든 노트 페이지**.
- 제목 메타데이터. 공백뿐인 본문은 허용하되 비공백 타이핑 본문은 아직 미지원으로 중단.

임의의 SDOCX 형식을 모두 지원하는 변환기는 아니다. 미지원 객체/스타일/텍스트/페이지 구조는 오류로 종료하며 불완전한 새 PDF를 게시하지 않는다. 기존 출력은 유지된다. PDF 출력은 같은 폴더의 임시 파일을 완성한 후 hard link로 새 이름에 게시하므로 hard link를 지원하는 파일시스템이 필요하다. 실제 Windows 잠금/파일시스템 동작은 아직 확인하지 않았다.

[전체 변환 구현 기록](docs/07_pdf_conversion.md) · [계획과 연구 문서](docs/README.md)

## 의존성과 배포

실행 의존성은 `requirements.txt`의 pypdf다. PyMuPDF, Pillow, Dietrich, sdocx_parser는 변환기에 필요 없다. 기존 `research/.venv`는 과거 연구/그림 제작용이다.

pypdf의 BSD-3-Clause 고지는 [third_party/pypdf-LICENSE.txt](third_party/pypdf-LICENSE.txt)에 보존했다. 배포물에도 해당 고지를 포함한다. 이 고지는 자체 프로젝트 전체의 배포 라이선스를 결정하는 것은 아니다.
