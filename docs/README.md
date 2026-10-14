# Samsung Notes → Obsidian 문서 안내

현재는 Windows 백업을 압축 해제된 SDOCX로 가정하고, 자체 SDOCX 해독 + pypdf 합성으로 전체 노트 PDF를 생성하는 CLI를 구현했다. 실제 Windows 감시/갱신은 다음 단계다.

제품 방향은 **Windows에서 sdoc/wdoc/SQLite 등을 포함한 삼성노트 폴더형 백업을 감시하고, 노트 전체를 텍스트 검색·선택 가능한 필기 포함 PDF로 변환해 Obsidian Vault에 갱신하는 것**이다.

최초 등록은 자동 또는 수동 선택이며 등록 후에는 모두 자동 갱신한다. 현재 출력은 열람용 사본으로 같은 경로에 교체한다. 향후 PDF++ 등을 통한 직접 수정과 백업·충돌 대응은 백로그 B10에 기록했다. 같은 경로로 백업 내용이 갱신된다는 가정은 실제 관측 전이다.

| 문서 | 용도 |
| --- | --- |
| [전체 PDF 변환](07_pdf_conversion.md) | pypdf 기반 CLI, 생성 결과, 지원/실패 범위 |
| [자체 파서 구조 연구](06_standalone_format_research.md) | 외부 의존성 없는 분석기, 실제 바이트 관측, 미해석 범위 |
| [개발 계획](01_plan.md) | 입력, 등록, 폴더 감시, PDF 생성·갱신 정책 |
| [백로그](02_backlog.md) | 현재 범위 밖의 기능과 조건부 작업 |
| [검증 계획](03_verification.md) | PDF 재현 품질과 파일 변경 처리 검증 |
| [실제 샘플 구조 조사](05_structure_research.md) | 기존 샘플의 관측 사실과 파서 제한. 과거 계획 기준 연구 기록 |
| [연구 실행 방법](../research/README.md) | 기존 구조 추출 스크립트 재현 |
| [이전 계획](archive/annotation_pipeline/01_plan.md) | 인용 추출·Markdown 병합 중심의 이전 계획 |

`04_sample_annotations_template.csv`는 이전 인용 추출 실험의 양식으로 보존한다. 새 PDF 변환 계획의 합격 기준이나 필수 입력 양식으로 사용하지 않는다.
