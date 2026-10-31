# Python environment

- For this project, use `C:\Users\THKim\Documents\Codex\SNo2md\.venv\Scripts\python.exe` as the Python interpreter.
- Run Python commands from the project root with `.\.venv\Scripts\python.exe`; do not use a system Python or the Codex bundled Python.
- Install all application dependencies from the project root with `.\.venv\Scripts\python.exe -m pip install -e ".\src[desktop]" -c research\desktop\requirements-build.txt`. The base package includes the Markdown engine; the desktop extra adds Windows GUI support.
- Keep `.venv/` local and untracked.
