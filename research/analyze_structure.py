"""Derive page maps and parser diagnostics from an inspect_samples.py run."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import struct
import traceback
import zipfile

from inspect_samples import write_json, sha256
import pymupdf as fitz
from kaitaistruct import KaitaiStream, BytesIO
from note_pipeline.input.samsung_notes.generated.samsung_note import SamsungNote


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def flatten(objects):
    for obj in objects:
        yield obj
        yield from flatten(obj.get("children", []))


def render_rotation_check(directory, mapped_words):
    rotated = [p for p in mapped_words if p["rotation"]]
    if not rotated:
        return
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from PIL import Image
    item = rotated[min(1, len(rotated) - 1)]
    with fitz.open(directory / "raw" / item["pdf_member"]) as pdf:
        page = pdf[item["pdf_index"]]
        pix = page.get_pixmap(alpha=False)
        bitmap = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        rect = page.rect * fitz.Matrix(item["fit_matrix"])
        fig, ax = plt.subplots(figsize=(12, 8.5))
        ax.imshow(bitmap, extent=(rect.x0, rect.x1, rect.y1, rect.y0))
        for word in item["words"]:
            x0, y0, x1, y1 = word["note_bbox"]
            ax.add_patch(Rectangle((x0, y0), x1-x0, y1-y0, fill=False, color="red", linewidth=0.5))
        ax.set_xlim(rect.x0, rect.x1)
        ax.set_ylim(rect.y1, rect.y0)
        ax.set_title(f"PDF page {page.number + 1}: rotated word boxes in note coordinates")
        fig.savefig(directory / "coordinate_check.png", dpi=120, bbox_inches="tight")
        plt.close(fig)


def analyze(directory):
    summary = load(directory / "summary.json")
    records = load(directory / "page_records.json")
    secondary = load(directory / "sdocx_parser_model.json")
    sidecars = load(directory / "sidecars.json")
    model = load(directory / "neutral_model.json")
    raw = directory / "raw"
    note_bytes = (raw / "note.note").read_bytes()
    partial = SamsungNote.__new__(SamsungNote)
    diagnostic = {}
    try:
        partial.__init__(KaitaiStream(BytesIO(note_bytes)))
    except Exception:
        diagnostic["note_parse_traceback"] = traceback.format_exc()
        diagnostic["note_failure_stream_position"] = partial._io.pos()
        diagnostic["string_id_block_size_at_failure"] = getattr(partial, "string_id_block_size", None)
        diagnostic["last_edited_page_index_at_failure"] = getattr(partial, "last_edited_page_index", None)
    diagnostic["note_header"] = {"format_version": struct.unpack_from("<I", note_bytes, 14)[0],
                                 "note_id_utf16_length": struct.unpack_from("<H", note_bytes, 18)[0],
                                 "property_flags_hex": hex(struct.unpack_from("<I", note_bytes, 10)[0])}
    diagnostic["model_note_id_is_fallback"] = model["note_id"] == "raw"
    diagnostic["body_chars"] = len(secondary["body"]["text"])
    diagnostic["body_nonwhitespace_chars"] = sum(not c.isspace() for c in secondary["body"]["text"])
    diagnostic["page_order_matches_secondary"] = [p["file"][:-5] for p in records] == [p["uuid"] for p in secondary["pages"]]
    diagnostic["page_order_matches_sidecar"] = [p["file"][:-5] for p in records] == [p["page_id"] for p in sidecars["pageIdInfo.dat"]["entries"]]
    diagnostic["page_uuid_matches_filename"] = all(p["file"][:-5] == p["metadata"]["page_uuid"] for p in records)
    diagnostic["pdf_byte_equal_to_zip_member"] = []
    with zipfile.ZipFile(summary["input"]) as archive:
        for pdf in summary["pdfs"]:
            path = Path(pdf["path"])
            diagnostic["pdf_byte_equal_to_zip_member"].append({"member": path.relative_to(raw).as_posix(),
                 "equal": archive.read(path.relative_to(raw).as_posix()) == path.read_bytes()})
    census = Counter()
    omitted = []
    page_rows = []
    mapped_words = []
    stroke_stats = []
    for record in records:
        index = record["index"]
        meta = record["metadata"]
        objects = list(flatten([obj for layer in record["layers"] for obj in layer["objects"]]))
        counts = Counter(str(o["object_type"]) for o in objects)
        census.update(counts)
        for obj in objects:
            if obj["object_type"] not in (1, 2, 3):
                omitted.append({"note_page_1based": index + 1, "page_id": meta["page_uuid"],
                                "object_type": obj["object_type"], "start": obj["start"],
                                "object_size": obj["object_size"], "record_end": obj["record_end"]})
        for stroke in record["strokes"]:
            points = stroke["points"]
            stroke_stats.append({"note_index": index, "point_count": len(points),
                                 "pressure_count": len(stroke.get("pressures", [])),
                                 "timestamp_count": len(stroke.get("timestamps", [])),
                                 "pen_size": stroke.get("pen_size"), "color": stroke.get("color_hex_argb"),
                                 "pen_name": (stroke.get("style") or {}).get("pen_name"),
                                 "bbox": [min(p[0] for p in points), min(p[1] for p in points),
                                          max(p[0] for p in points), max(p[1] for p in points)] if points else None})
        for background in record["backgrounds"] or [None]:
            row = {"note_page_1based": index + 1, "page_id": meta["page_uuid"],
                   "canvas_width": meta["page_width"], "canvas_height": meta["page_height"],
                   "pdf_member": "", "pdf_page_1based": "", "pdf_rotation": "", "pdf_rect_in_note": "",
                   "strokes": len(record["strokes"]), "images": len(record["images"]),
                   "text_fields": len(record["text_fields"]), "other_object_count": sum(v for k,v in counts.items() if k not in ('1','2','3')),
                   "in_neutral_model": any(p["page_id"] == meta["page_uuid"] for p in model["pages"]),
                   "status": "no_pdf_background"}
            if background:
                pdf_path = Path(background["media_path"])
                with fitz.open(pdf_path) as pdf:
                    page = pdf[background["page_index"]]
                    rect = fitz.Rect(background["rect"])
                    # PyMuPDF text boxes are unrotated. Rotate before fitting the displayed PDF to note rect.
                    fit = page.rect.torect(rect)
                    words = []
                    for w in page.get_text("words"):
                        box = fitz.Rect(w[:4]) * page.rotation_matrix * fit
                        words.append({"text": w[4], "pdf_bbox_unrotated": w[:4], "note_bbox": list(box),
                                      "block": w[5], "line": w[6], "word": w[7]})
                    mapped_words.append({"note_index": index, "page_id": meta["page_uuid"], "pdf_index": page.number,
                                         "pdf_member": pdf_path.relative_to(raw).as_posix(), "rotation": page.rotation,
                                         "rotation_matrix": list(page.rotation_matrix), "fit_matrix": list(fit), "words": words})
                    row.update(pdf_member=pdf_path.relative_to(raw).as_posix(), pdf_page_1based=page.number + 1,
                               pdf_rotation=page.rotation, pdf_rect_in_note=json.dumps(background["rect"]), status="mapped")
            page_rows.append(row)
    diagnostic["raw_object_census"] = dict(census)
    diagnostic["secondary_object_census"] = dict(sum((Counter(p["census"]) for p in secondary["pages"]), Counter()))
    diagnostic["objects_without_model_support"] = omitted
    diagnostic["neutral_unsupported_count"] = summary["element_counts"].get("UnsupportedElement", 0)
    diagnostic["stroke_count"] = len(stroke_stats)
    diagnostic["stroke_point_count"] = sum(s["point_count"] for s in stroke_stats)
    diagnostic["all_pressure_lengths_match"] = all(s["point_count"] == s["pressure_count"] for s in stroke_stats)
    diagnostic["all_timestamp_lengths_match"] = all(s["point_count"] == s["timestamp_count"] for s in stroke_stats)
    diagnostic["pen_names_resolved"] = sum(bool(s["pen_name"]) for s in stroke_stats)
    diagnostic["input_still_unchanged"] = sha256(Path(summary["input"]).read_bytes()) == summary["input_sha256"]
    write_json(directory / "diagnostics.json", diagnostic)
    write_json(directory / "stroke_inventory.json", stroke_stats)
    write_json(directory / "pdf_words_in_note.json", mapped_words)
    render_rotation_check(directory, mapped_words)
    with (directory / "page_map.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(page_rows[0]))
        writer.writeheader()
        writer.writerows(page_rows)
    print(directory.name, {k: diagnostic[k] for k in ['raw_object_census', 'stroke_point_count', 'body_nonwhitespace_chars', 'input_still_unchanged']})
    return {"sample": directory.name, **diagnostic}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    root = args.run_directory.resolve()
    results = [analyze(p) for p in sorted(root.iterdir()) if p.is_dir() and (p / "summary.json").exists()]
    write_json(root / "diagnostics.json", results)


if __name__ == "__main__":
    main()
