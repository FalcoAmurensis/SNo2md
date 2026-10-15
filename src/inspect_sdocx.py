"""Compatibility entry point for the standard-library SDOCX inspector."""
# Support the historical `python -I -S src/inspect_sdocx.py` invocation.
import sys
from pathlib import Path
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from sno2md.binary import *

if __name__ == '__main__':
    main()
