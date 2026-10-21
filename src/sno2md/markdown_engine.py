"""Extract original PDF pages in memory; caches live for one note export only."""
from threading import Lock, get_ident

from .model import RenderError


_initialization_lock = Lock()
_configured_layout = None


def load_engine():
    """Initialize the pinned Layout engine with non-spinning ONNX sessions.

    Layout 1.28.2 creates its sessions during import and exposes no session
    options. Scope the constructor hook to this thread and initialization only;
    never leave ONNX patched for other models in an embedding application.
    """
    global _configured_layout
    with _initialization_lock:
        import onnxruntime
        import pymupdf

        if _configured_layout is not None and pymupdf._get_layout is _configured_layout:
            import pymupdf4llm
            pymupdf4llm.use_layout(True)
            return pymupdf, pymupdf4llm

        original = onnxruntime.InferenceSession.__init__
        owner = get_ident()

        def initialize(session, path_or_bytes, sess_options=None, *args, **kwargs):
            if get_ident() == owner:
                sess_options = sess_options if sess_options is not None else onnxruntime.SessionOptions()
                sess_options.add_session_config_entry('session.intra_op.allow_spinning', '0')
                sess_options.add_session_config_entry('session.inter_op.allow_spinning', '0')
            original(session, path_or_bytes, sess_options, *args, **kwargs)

        previous_layout = pymupdf._get_layout
        onnxruntime.InferenceSession.__init__ = initialize
        try:
            # Also replace a Layout model imported earlier by an API caller.
            # Existing sessions cannot be reconfigured after construction.
            pymupdf._get_layout = None
            import pymupdf4llm
            pymupdf4llm.use_layout(True)
            _configured_layout = pymupdf._get_layout
        except Exception:
            pymupdf._get_layout = previous_layout
            raise
        finally:
            onnxruntime.InferenceSession.__init__ = original
        return pymupdf, pymupdf4llm


class SourceMarkdown:
    def __init__(self, note):
        try:
            pymupdf, pymupdf4llm = load_engine()
        except ImportError as error:
            raise RenderError("Markdown export requires pip install 'sno2md[markdown]'") from error
        self.note, self.pdf, self.engine = note, pymupdf, pymupdf4llm
        self.documents, self.pages = {}, {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        for document in self.documents.values():
            document.close()
        self.documents.clear()
        self.pages.clear()

    def extract(self, asset, page_index):
        key = (asset, page_index)
        if key in self.pages:
            return self.pages[key]
        try:
            if asset not in self.documents:
                self.documents[asset] = self.pdf.open(stream=self.note.files[asset], filetype='pdf')
            document = self.documents[asset]
            if not 0 <= page_index < len(document):
                raise ValueError('source PDF page index out of bounds')
            chunks = self.engine.to_markdown(
                document, pages=[page_index], page_chunks=True, use_ocr=False,
                force_ocr=False, write_images=False, embed_images=False,
                force_text=True, show_progress=False, header=True, footer=True,
            )
            if (not isinstance(chunks, list) or len(chunks) != 1 or
                    not isinstance(chunks[0].get('text'), str) or
                    chunks[0].get('metadata', {}).get('page_number') != page_index + 1):
                raise ValueError('unexpected Markdown page result')
            self.pages[key] = chunks[0]['text']
            return self.pages[key]
        except Exception as error:
            raise RenderError(f'Markdown extraction failed for {asset}, page {page_index + 1}: {error}') from error
