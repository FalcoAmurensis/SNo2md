"""Read-only SDOCX research using pinned upstream parsers; not a sync tool."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import fields, is_dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback
import zipfile

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT / "private/upstream"
sys.path.insert(0, str(UPSTREAM / "Dietrich"))
sys.path.insert(0, str(UPSTREAM / "sdocx_parser"))
os.environ.setdefault("MPLBACKEND", "Agg")

import pymupdf as fitz
from kaitaistruct import KaitaiStruct
from note_pipeline.input.samsung_notes.importer import SamsungNotesImporter
from note_pipeline.input.samsung_notes.package import list_page_files
from note_pipeline.input.samsung_notes.parsers import SpenNotesPageParser
from note_pipeline.input.samsung_notes.sidecars import parse_end_tag, parse_media_info, parse_page_id_info
from note_pipeline.input.samsung_notes.source import load_samsung_note_source
from note_pipeline.input.samsung_notes.note_adapters import note_source_to_metadata
from note_pipeline.input.samsung_notes.constants import PDF_OVERLAY_TAGS_TO_STRIP
from note_pipeline.model import PdfBackgroundElement, StrokeElement, UnsupportedElement
from note_pipeline.output.raster import render_surface_to_png
import sdocx_parser


def serial(value):
    if isinstance(value, KaitaiStruct):
        return {"_type": type(value).__name__, **{k: serial(v) for k, v in vars(value).items() if not k.startswith("_")}}
    if is_dataclass(value):
        return {"_type": type(value).__name__, **{f.name: serial(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, bytes):
        return {"encoding": "hex", "data": value.hex()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [serial(v) for v in value]
    return value


def write_json(path, value):
    path.write_text(json.dumps(serial(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def pdf_inventory(path):
    with fitz.open(path) as doc:
        pages = []
        for page in doc:
            words = page.get_text("words")
            annotations = [{"xref": a.xref, "type": a.type, "rect": list(a.rect),
                            "vertices": a.vertices, "info": a.info} for a in page.annots() or []]
            streams = b"\n".join(doc.xref_stream(x) or b"" for x in page.get_contents())
            pages.append({"index": page.number, "rect": list(page.rect), "cropbox": list(page.cropbox),
                          "rotation": page.rotation, "word_count": len(words), "words": words,
                          "text": page.get_text(), "annotations": annotations,
                          "samsung_overlay_tokens": {tag: streams.count(tag.encode()) for tag in
                              PDF_OVERLAY_TAGS_TO_STRIP}})
        return {"path": str(path), "sha256": sha256(path.read_bytes()), "page_count": len(doc),
                "metadata": doc.metadata, "is_repaired": doc.is_repaired, "pages": pages}


def inspect(path, out, render_count):
    target = out / path.stem
    target.mkdir()
    raw = target / "raw"
    raw.mkdir()
    before = sha256(path.read_bytes())
    summary = {"input": str(path), "input_sha256": before, "input_bytes": path.stat().st_size, "errors": []}
    with zipfile.ZipFile(path) as archive:
        inventory = []
        for entry in archive.infolist():
            dest = (raw / entry.filename).resolve()
            if not dest.is_relative_to(raw.resolve()):
                raise ValueError(f"Archive path escapes destination: {entry.filename}")
            if entry.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            data = archive.read(entry)  # ZIP CRC is checked by zipfile.
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            inventory.append({"path": entry.filename, "bytes": len(data), "compressed_bytes": entry.compress_size,
                              "crc32": f"{entry.CRC:08x}", "header_hex": data[:32].hex()})
    write_json(target / "archive_inventory.json", inventory)
    summary["archive_entries"] = len(inventory)
    sidecars = {}
    for name, parser in [("pageIdInfo.dat", parse_page_id_info), ("media/mediaInfo.dat", parse_media_info),
                         ("end_tag.bin", parse_end_tag)]:
        sidecars[name] = parser((raw / name).read_bytes())
    write_json(target / "sidecars.json", sidecars)
    source = load_samsung_note_source(str(raw / "note.note"))
    metadata = note_source_to_metadata(source)
    write_json(target / "note_metadata.json", {"parse_error": source.parse_error, "metadata": metadata})
    summary["note_id"] = metadata.get("note_id")
    summary["end_tag_note_id"] = sidecars["end_tag.bin"].get("note_id")
    summary["note_parse_error"] = source.parse_error
    summary["format_version"] = metadata.get("format_version")
    pdfs = [pdf_inventory(p) for p in sorted(raw.rglob("*.pdf"))]
    write_json(target / "pdf_inventory.json", pdfs)
    summary["pdfs"] = [{"path": p["path"], "page_count": p["page_count"], "sha256": p["sha256"],
                         "is_repaired": p["is_repaired"], "text_pages": sum(bool(x["word_count"]) for x in p["pages"]),
                         "annotations": sum(len(x["annotations"]) for x in p["pages"])} for p in pdfs]
    pages = []
    for index, page_path in enumerate(list_page_files(str(raw))):
        record = {"index": index, "file": Path(page_path).name}
        try:
            parser = SpenNotesPageParser(page_path, note_root=str(raw))
            record.update(metadata=parser.extract_page_metadata(), backgrounds=parser.extract_background_records(),
                          strokes=parser.extract_stroke_records(), images=parser.extract_image_records(),
                          text_fields=parser.extract_text_field_records())
            # Diagnostic use of the upstream private layer API, pinned by commit.
            layers = parser._parse_layers()
            record["layers"] = layers
        except Exception:
            record["error"] = traceback.format_exc()
            summary["errors"].append({"page": index, "error": record["error"]})
        pages.append(record)
    write_json(target / "page_records.json", pages)
    summary["raw_pages"] = len(pages)
    summary["raw_strokes"] = sum(len(p.get("strokes", [])) for p in pages)
    summary["raw_images"] = sum(len(p.get("images", [])) for p in pages)
    summary["raw_text_fields"] = sum(len(p.get("text_fields", [])) for p in pages)
    importer = SamsungNotesImporter()
    try:
        note = importer.import_path(str(raw))
        write_json(target / "neutral_model.json", note)
        summary["model_pages"] = len(note.pages)
        summary["element_counts"] = dict(Counter(type(e).__name__ for p in note.pages for e in p.elements))
        summary["page_mapping"] = [{"note_index": p.index, "page_id": p.page_id, "size": [p.width, p.height],
                                    "pdf_backgrounds": [serial(e) for e in p.elements if isinstance(e, PdfBackgroundElement)],
                                    "counts": dict(Counter(type(e).__name__ for e in p.elements))} for p in note.pages]
        rendered = []
        candidates = [p for p in note.pages if any(isinstance(e, StrokeElement) for e in p.elements)]
        for page in candidates[:render_count]:
            dest = target / f"preview_note_page_{page.index + 1:03d}.png"
            try:
                render_surface_to_png(note, page, str(dest), thickness_scale=0.6, output_scale=0.75)
                rendered.append({"path": str(dest), "note_index": page.index,
                                 "unsupported_elements": sum(isinstance(e, UnsupportedElement) for e in page.elements)})
            except Exception:
                summary["errors"].append({"render_page": page.index, "error": traceback.format_exc()})
        summary["previews"] = rendered
    except Exception:
        summary["errors"].append({"importer": traceback.format_exc()})
    finally:
        importer.close()
    try:
        secondary = sdocx_parser.parse_sdocx(path)
        write_json(target / "sdocx_parser_model.json", secondary)
        summary["sdocx_parser"] = {"pages": len(secondary.pages), "body_chars": len(secondary.body.text),
                                    "nonwhitespace_body_chars": sum(not c.isspace() for c in secondary.body.text),
                                    "text_fields": sum(len(p["texts"]) for p in secondary.pages), "skipped": secondary.skipped}
    except Exception:
        summary["sdocx_parser"] = {"error": traceback.format_exc()}
    summary["input_unchanged"] = before == sha256(path.read_bytes())
    write_json(target / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ["page_mapping", "previews", "pdfs"]}, ensure_ascii=False), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "private/samples")
    parser.add_argument("--output", type=Path, required=True, help="New directory; refuses to overwrite an existing run")
    parser.add_argument("--render-count", type=int, default=2)
    args = parser.parse_args()
    samples = sorted(args.input.resolve().glob("*.sdocx"))
    if not samples:
        parser.error("No .sdocx input found")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    provenance = {"python": platform.python_version(), "platform": platform.platform(),
                  "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
                  "upstream": {p.name: subprocess.check_output(["git", "-C", str(p), "rev-parse", "HEAD"], text=True).strip()
                               for p in sorted(UPSTREAM.iterdir()) if (p / ".git").exists()}}
    write_json(out / "environment.json", provenance)
    summaries = [inspect(p, out, args.render_count) for p in samples]
    write_json(out / "summary.json", summaries)


if __name__ == "__main__":
    main()
