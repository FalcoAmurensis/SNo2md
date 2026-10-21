"""Current observations shared by the coordinator, CLI and desktop workers."""
from pathlib import Path

from .folder_selection import inventory, selection_for, enabled
from .sync_storage import inspect_outputs

GROUPS = {'active': '활성', 'source_missing': '원본 없음',
          'output_missing': 'Vault 파일 없음', 'retired': '관리 중단'}


def group(entry, observation):
    if entry.get('sync_policy', 'managed') == 'retired':
        return 'retired'
    if observation.get('missing'):
        return 'output_missing'
    if observation.get('source_exists') is False:
        return 'source_missing'
    return 'active'


def observe(config, state, found=None, folders=None):
    from .sync import discover
    try:
        if not Path(config['vault']).is_dir():
            raise OSError('Vault unavailable; output presence is unknown')
        found = discover(config['backup']) if found is None else found
        folders = inventory(config['backup'], found) if folders is None else folders
    except (OSError, ValueError) as error:
        return {key: {'source_exists': None, 'missing': [], 'conflicts': {},
                      'unavailable': str(error), 'group': group(entry, {})}
                for key, entry in state['notes'].items()}
    observations = {}
    for key, entry in state['notes'].items():
        item = {'source_exists': key in found, 'missing': [], 'conflicts': {}}
        category = folders['membership'].get(key, entry.get('selection_folder', 'root'))
        item['excluded'] = not enabled(folders['tree'], selection_for(config), category)
        try:
            inspection = inspect_outputs(config, entry)
            item.update(missing=inspection['missing'], conflicts=inspection['conflicts'],
                        md_resolvable=bool(inspection['md_parts'] and
                                          entry.get('success', {}).get('md_managed_hash')),
                        md_hash=inspection['hashes'].get('md'))
        except (OSError, ValueError) as error:
            item['unavailable'] = str(error)
        item['group'] = group(entry, item)
        observations[key] = item
    return observations


def describe(item):
    """User-facing description, without ownership hashes or internal field names."""
    if item.get('unavailable'):
        return '관찰 미확인: ' + item['unavailable']
    lines = ['원본: ' + ('있음' if item.get('source_exists') else '없음'),
             'Vault 누락: ' + (', '.join(item.get('missing', [])) or '없음')]
    if item.get('excluded'):
        lines.append('폴더 선택에서 제외됨')
    lines.extend(item.get('conflicts', {}).values())
    return '\n'.join(lines)
