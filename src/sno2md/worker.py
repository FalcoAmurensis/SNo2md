"""Isolated document conversion. Workers never open sync state or publish to Vault."""
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import time


def worker_count(value):
    if value == 'auto':
        return value
    if isinstance(value, bool) or str(value) not in {str(n) for n in range(1, 9)}:
        raise ValueError('workers must be auto or an integer from 1 to 8')
    return int(value)


def choose_workers(value, jobs):
    policy = worker_count(value)
    # psutil is included by the Markdown engine. Remain usable without that extra.
    try:
        import psutil
        available = psutil.virtual_memory().available
    except ImportError:
        available = None
    requested = min(2, max(1, (os.cpu_count() or 2)//2)) if policy == 'auto' else policy
    # Leave 2 GiB for the desktop; budget 2 GiB per converter initially.
    memory_limit = max(1, int((available - 2*1024**3)//(2*1024**3))) if available is not None else 1
    return max(1, min(requested, jobs, memory_limit)), available


def initialize_worker():
    # Ctrl+C is handled by the coordinator: allow in-flight documents to finish.
    signal.signal(signal.SIGINT, signal.SIG_IGN)


@dataclass(frozen=True)
class ConversionJob:
    snapshot: str
    output: str
    name: str
    width_scale: float
    submitted: float


def convert_job(job):
    from .api import export_note
    from .model import ConversionOptions
    stages = {}
    started = time.perf_counter()
    from .sync_storage import store_lock
    with store_lock(Path(job.snapshot).parent):
        result = export_note(Path(job.snapshot), Path(job.output), name=job.name,
                             options=ConversionOptions(width_scale=job.width_scale), timings=stages)
    finished = time.perf_counter()
    try:
        import psutil
        memory = psutil.Process().memory_info()
        rss = getattr(memory, 'peak_wset', memory.rss)
    except ImportError:
        rss = None
    return {'output': str(result.pdf.parent), 'counts': result.counts,
            'pid': os.getpid(), 'started': started, 'finished': finished,
            'queue_seconds': started-job.submitted, 'seconds': finished-started,
            'peak_rss': rss, 'stages': stages}
