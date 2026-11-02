"""Extract original PDF pages in memory; caches live for one note export only."""
import base64
import sys
from threading import Lock, get_ident

from .model import RenderError


_initialization_lock = Lock()
_configured_layout = None


def load_engine():
    """Initialize the pinned Layout engine with non-spinning ONNX sessions.

    Layout 2.0 creates its sessions during import and exposes no session
    options. Scope the constructor hook to this thread and initialization only;
    never leave ONNX patched for other models in an embedding application.
    """
    global _configured_layout
    with _initialization_lock:
        import onnxruntime
        loaded = sys.modules.get('pymupdf')
        if (_configured_layout is not None and loaded is not None and
                loaded._get_layout is _configured_layout):
            import pymupdf4llm
            pymupdf4llm.use_layout(True)
            return loaded, pymupdf4llm

        original = onnxruntime.InferenceSession.__init__
        owner = get_ident()

        def initialize(session, path_or_bytes, sess_options=None, *args, **kwargs):
            if get_ident() == owner:
                sess_options = sess_options if sess_options is not None else onnxruntime.SessionOptions()
                sess_options.add_session_config_entry('session.intra_op.allow_spinning', '0')
                sess_options.add_session_config_entry('session.inter_op.allow_spinning', '0')
            original(session, path_or_bytes, sess_options, *args, **kwargs)

        previous_layout = loaded._get_layout if loaded is not None else None
        pymupdf = loaded
        onnxruntime.InferenceSession.__init__ = initialize
        try:
            # 2.0 initializes Layout on import of pymupdf itself, so the hook
            # must already be installed before that first import.
            import pymupdf
            if loaded is not None:
                # Existing sessions cannot be reconfigured after construction.
                pymupdf._get_layout = None
            import pymupdf4llm
            pymupdf4llm.use_layout(True)
            _configured_layout = pymupdf._get_layout
        except Exception:
            if pymupdf is not None:
                pymupdf._get_layout = previous_layout
            raise
        finally:
            onnxruntime.InferenceSession.__init__ = original
        return pymupdf, pymupdf4llm


class SourceMarkdown:
    def __init__(self, note, object_policy='image'):
        if object_policy not in {'image','native'}:
            raise ValueError('unknown source-object display policy')
        try:
            pymupdf, pymupdf4llm = load_engine()
        except ImportError as error:
            raise RenderError("Markdown engine dependencies are missing; reinstall the project's declared dependencies") from error
        self.note, self.pdf, self.engine = note, pymupdf, pymupdf4llm
        self.documents, self.pages = {}, {}
        self.object_policy = object_policy
        self.object_renderers, self.object_reports = {}, {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        for document in self.documents.values():
            document.close()
        self.documents.clear()
        self.pages.clear()
        self.object_renderers.clear()
        self.object_reports.clear()

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
                force_ocr=False, write_images=False, embed_images=True,
                force_text=True, show_progress=False, header=True, footer=True,
            )
            if (not isinstance(chunks, list) or len(chunks) != 1 or
                    not isinstance(chunks[0].get('text'), str) or
                    chunks[0].get('metadata', {}).get('page_number') != page_index + 1):
                raise ValueError('unexpected Markdown page result')
            text = chunks[0]['text']
            if self.object_policy == 'image':
                from .object_rendering import ObjectRenderer, render_page_objects
                renderer = self.object_renderers.setdefault(asset, ObjectRenderer(document))
                text, report = render_page_objects(chunks[0], renderer)
                self.object_reports[key] = report
            if not text.strip() and document[page_index].get_images():
                # Layout can return no boxes for a scan. Preserve its visual
                # content without OCR or silently returning an empty success.
                image = document[page_index].get_pixmap(dpi=150, alpha=False).tobytes('png')
                text = ('![Source page image](data:image/png;base64,' +
                        base64.b64encode(image).decode('ascii') + ')\n')
            self.pages[key] = text
            return self.pages[key]
        except Exception as error:
            raise RenderError(f'Markdown extraction failed for {asset}, page {page_index + 1}: {error}') from error
