# 구현 출처와 범위

작성일: 2026-10-14

`inspect_sdocx.py`는 이 프로젝트를 위해 새로 작성한 Python 표준 라이브러리 기반 분석기다. Dietrich, sdocx_parser 및 다른 저장소의 구현 코드나 패키지를 복사·import·호출하지 않는다. 외부 파서가 생성한 JSON도 실행 입력으로 사용하지 않는다.

바이너리 필드 배치와 압축 규칙은 아래 **포맷 문서**를 참고하고 로컬 SDOCX 세 개의 실제 바이트/레코드 경계/배열 길이와 대조했다. 문서의 코드 예제를 가져오는 대신 bounded reader, 레코드 순회, 컨테이너 처리를 직접 작성했다. 이전 연구에서 외부 구현을 열람·실행한 이력이 있으므로 이 작업을 정식 clean-room 절차라고 부르지 않는다.

## 포맷 참고 문서

Dietrich 커밋 `bb9c9202a8ab2bf96347e6cd0b16eaa354b80483`의 `sdocx_specification/`:

- [page_format_specification.md](https://github.com/Downhillghost/Dietrich/blob/bb9c9202a8ab2bf96347e6cd0b16eaa354b80483/sdocx_specification/page_format_specification.md): 페이지 속성, 레이어/객체 프레임, stroke-own 및 압축 좌표/배열.
- [page_id_info_format_specification.md](https://github.com/Downhillghost/Dietrich/blob/bb9c9202a8ab2bf96347e6cd0b16eaa354b80483/sdocx_specification/page_id_info_format_specification.md): 페이지 순서.
- [media_info_format_specification.md](https://github.com/Downhillghost/Dietrich/blob/bb9c9202a8ab2bf96347e6cd0b16eaa354b80483/sdocx_specification/media_info_format_specification.md): 미디어 bind ID.
- [note_format_specification.md](https://github.com/Downhillghost/Dietrich/blob/bb9c9202a8ab2bf96347e6cd0b16eaa354b80483/sdocx_specification/note_format_specification.md): 노트 고정 헤더와 객체 길이.

이번에는 기존 로컬 체크아웃의 문서를 읽었다. 원격 raw URL 접근은 실패했다. 참고 저장소의 당시 라이선스 표기는 MIT이며 출처 추적을 위해 커밋과 문서 경로를 보존한다.

## 기존 연구와의 분리

GPLv3인 sdocx_parser, 라이선스 파일을 확인하지 못했던 snotebackup/Windows exporter는 새 분석기의 구성 요소가 아니다. 기존 `research/requirements.txt`, `.venv/`, `upstream/`은 과거 연구 재현용이며 새 분석기를 실행하거나 배포할 때 포함할 필요가 없다.

이번 작업은 외부 구현 코드에 대한 실행/배포 의존성을 제거했다. 모든 법적 쟁점이 사라졌다는 보증은 아니며 자체 프로젝트의 배포 라이선스는 별도 결정 사항이다. 샘플 노트와 내장 논문 PDF의 배포 권리도 프로그램 코드와 별개다.

## 후속 전체 PDF 변환

사용자 승인으로 `convert_sdocx.py`는 pypdf를 사용한다. BSD-3-Clause 고지는 루트 `third_party/pypdf-LICENSE.txt`에 보존했다. 도형/이미지 경로는 자체 바이트 조사에 기반하며 관측 프로필과 제한은 `docs/07_pdf_conversion.md`에 기록했다. 앞선 분석기 자체의 표준 라이브러리 전용 실행 조건은 유지된다.
