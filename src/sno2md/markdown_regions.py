"""Byte-preserving boundary between generated Markdown and user notes."""
import hashlib

START = b'<!-- sno2md:generated:start -->'
END = b'<!-- sno2md:generated:end -->'
DEFAULT_USER = '\n## 사용자 메모\n'.encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def split(data):
    lines = data.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip(b'\r\n') == START]
    ends = [i for i, line in enumerate(lines) if line.rstrip(b'\r\n') == END]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError('Markdown generated boundaries are missing, duplicated or out of order')
    start, end = starts[0], ends[0]
    if (not lines or lines[0].rstrip(b'\r\n') != b'---' or start < 2
            or lines[start-1].rstrip(b'\r\n') != b'---'
            or any(line.rstrip(b'\r\n') == b'---' for line in lines[1:start-1])
            or not lines[end].endswith(b'\n')):
        raise ValueError('Markdown boundary must immediately follow frontmatter and end with a newline')
    offset = sum(map(len, lines[:end+1]))
    return data[:offset], data[offset:]


def create(frontmatter, body):
    data = frontmatter + START + b'\n' + body + b'\n' + END + b'\n' + DEFAULT_USER
    split(data)  # Reject reserved marker lines in extracted source text, too.
    return data
