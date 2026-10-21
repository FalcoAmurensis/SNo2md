"""Presentation and action eligibility from already-observed state; no file I/O."""
STATUS = {'deferred': '보류', 'failed': '처리 실패', 'missing_output': '저장 파일 없음',
          'excluded': '가져오지 않음', 'pending': '처리 대기', 'waiting': '백업 저장 완료 대기',
          'converting': '문서 변환 중', 'queued': '처리 순서 대기', 'ready_to_publish': '파일 저장 대기',
          'synced': '최신 상태', 'missing': '백업에서 찾을 수 없음', 'conflict': '수정 확인 필요', 'error': '처리 실패',
          'managed': '관리 재개', 'retired': '관리 중단'}
GROUPS = {'active': '동기화 문서', 'source_missing': '백업에서 찾을 수 없음',
          'output_missing': '저장 폴더/Vault 파일 없음', 'retired': '관리 중단'}
FILTERS = ('전체', '최신 상태', '대기·진행 중', '확인 필요')
PENDING = {'pending', 'waiting', 'queued', 'converting', 'ready_to_publish'}
RETRYABLE = {'error', 'conflict', 'missing', 'pending', 'waiting'}


def execution(busy, kind, stopping):
    if not busy:
        return 'idle'
    if stopping:
        return 'stopping'
    return 'watch' if kind == 'watch' else 'working'


def close_action(busy, kind, stopping, tray):
    mode = execution(busy, kind, stopping)
    return ('hide' if tray else 'confirm') if mode == 'watch' else 'exit'


def needs_attention(entry, item):
    return bool(item.get('missing') or item.get('conflicts') or item.get('unavailable') or
                item.get('source_exists') is False or entry.get('status') in ('missing', 'missing_output', 'conflict', 'error'))


def matches(key, entry, item, query, state_filter):
    haystack = ' '.join((key, entry.get('name', ''), entry.get('folder', ''))).casefold()
    if query.strip().casefold() not in haystack:
        return False
    if state_filter == '최신 상태':
        return entry.get('status') == 'synced' and not needs_attention(entry, item)
    if state_filter == '대기·진행 중':
        return entry.get('status') in PENDING
    return needs_attention(entry, item) if state_filter == '확인 필요' else True


def eligible(action, entry, item):
    retired = entry.get('sync_policy', 'managed') == 'retired'
    if action == 'retire':
        return '' if not retired else '이미 관리가 중단된 문서입니다.'
    if action == 'resume':
        return '' if retired else '이미 관리 중인 문서입니다.'
    if retired:
        return '먼저 관리를 재개하세요.'
    if item.get('excluded'):
        return '설정에서 이 문서의 폴더를 포함하세요.'
    if item.get('unavailable'):
        return '백업과 저장 폴더의 접근 상태를 확인하세요.'
    if item.get('source_exists') is not True:
        return '현재 백업에서 문서를 찾을 수 없습니다.'
    if action == 'restore':
        if not entry.get('success') or not item.get('missing'):
            return '다시 만들 누락 파일이 없습니다.'
        if item.get('conflicts'):
            return '남은 파일의 수정을 먼저 확인하세요. Markdown 충돌은 충돌 해결을 이용하세요.'
        return ''
    if action == 'resolve':
        if not entry.get('success') or set(item.get('conflicts', {})) != {'md'} or not item.get('md_hash'):
            return 'Markdown 생성 영역 충돌만 해결할 수 있습니다. PDF 변경이나 경로 점유는 별도 확인이 필요합니다.'
        return ''
    if action == 'retry':
        if item.get('missing'):
            return '누락 파일은 저장 폴더/Vault에 다시 저장으로 처리하세요.'
        return '' if entry.get('status') in RETRYABLE else '다시 시도할 작업이 없습니다.'
    return ''


def targets(action, keys, notes, observations):
    accepted, skipped = set(), []
    for key in sorted(keys):
        reason = eligible(action, notes[key], observations.get(key, {}))
        if reason:
            skipped.append({'source': key, 'status': 'deferred', 'message': reason, 'user_message': reason})
        else:
            accepted.add(key)
    return accepted, skipped


def guidance(entry, item):
    lines = []
    if entry.get('sync_policy') == 'retired':
        lines.append('자동 갱신을 중단했습니다. 남은 파일과 동기화 이력은 유지합니다. 필요하면 관리를 재개하세요.')
    if item.get('excluded'):
        lines.append('가져올 폴더에서 제외된 문서입니다. 다시 가져오려면 설정에서 폴더를 포함하세요.')
    if item.get('unavailable'):
        lines.append('백업 또는 저장 폴더에 접근하지 못해 파일 상태를 확인할 수 없습니다. 경로와 연결 상태를 확인하세요.')
    if item.get('source_exists') is False:
        lines.append('현재 백업에서 문서를 찾을 수 없습니다. 백업 위치와 백업 완료 여부를 확인하세요.')
    if item.get('missing'):
        lines.append('저장 파일 없음: ' + ', '.join(ext.upper() for ext in item['missing']) +
                     '. 현재 백업으로 다시 만들려면 저장 폴더/Vault에 다시 저장을 선택하세요. 삭제된 사용자 메모를 복구하는 기능은 아닙니다.')
    conflicts = item.get('conflicts', {})
    if 'md' in conflicts:
        lines.append('Markdown 생성 영역이나 경계가 변경되었습니다. 충돌 해결에서 수정본 보관 후 재개할 수 있습니다.')
        if not item.get('md_resolvable'):
            lines.append('작성 영역을 구분할 수 없어 전체 수정본을 보관해야 합니다. 새 파일의 사용자 메모로 필요한 내용을 직접 옮기세요.')
    if 'pdf' in conflicts:
        lines.append('PDF 변경 또는 파일 경로 점유가 발견되었습니다. 파일과 경로를 직접 확인하세요. Markdown 충돌 해결로 덮어쓸 수 없습니다.')
    if entry.get('status') in ('error', 'conflict') and not conflicts:
        lines.append('처리가 완료되지 않았습니다. 아래 기술 정보의 원문 오류를 확인한 뒤 다시 시도하세요.')
    return '\n'.join(lines) or STATUS.get(entry.get('status'), '문서 상태를 확인하세요.')


def status_text(entry, item):
    label = STATUS.get(entry.get('status'), '상태 확인 필요')
    if item.get('missing'):
        label += ' · ' + '/'.join(ext.upper() for ext in item['missing']) + ' 없음'
    if item.get('source_exists') is False and entry.get('status') != 'missing':
        label += ' · 백업 없음'
    if item.get('conflicts') and entry.get('status') != 'conflict':
        label += ' · 수정 확인'
    if item.get('unavailable'):
        label += ' · 접근 미확인'
    return label
