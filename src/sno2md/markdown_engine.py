"""Extract original PDF pages in memory; caches live for one note export only."""
from .model import RenderError


class SourceMarkdown:
    def __init__(self, note):
        try:
            import pymupdf
            import pymupdf4llm
            pymupdf4llm.use_layout(True)
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
