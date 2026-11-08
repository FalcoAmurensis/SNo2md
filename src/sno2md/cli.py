"""CLI shared by the package entry point and legacy script."""
import argparse
import json
import sys
from pathlib import Path
from .api import convert_pdf, export_note, new_path, protect_source
from .model import ConversionOptions
from .layout_renderer import MODES


def main(argv=None):
    from multiprocessing import freeze_support
    freeze_support()
    # Redirected Windows output otherwise uses the legacy code page (e.g. cp949).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'sync':
        from .sync_cli import main as sync_main
        return sync_main(argv[1:])
    parser = argparse.ArgumentParser(description='SDOCX to PDF, optionally with Markdown.',
                                     epilog='Backup/Vault synchronization: sno2md sync --help')
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path,help='New PDF path, or output/Vault directory with --bundle')
    parser.add_argument('--width-scale',type=float,default=0.5)
    parser.add_argument('--bundle',action='store_true',help='Export PDF + MD')
    parser.add_argument('--name',help='Output filename stem for --bundle')
    parser.add_argument('--image-storage', choices=('external', 'embedded'), default='external',
                        help='Bundle images: separate files (default) or embedded data URIs')
    parser.add_argument('--repeat-distance', type=int, default=8, help='64-bit pHash distance, 0..32 (default 8)')
    parser.add_argument('--no-image-review', action='store_true', help='Preserve all images without review analysis')
    parser.add_argument('--markdown-mode', choices=MODES, default='classic', help='Source Markdown layout policy')
    parser.add_argument('--layout-report', type=Path, help='Write layout diagnostics to a new JSON file')
    args = parser.parse_args(argv)
    if not args.bundle and args.name is not None:
        parser.error('--name requires --bundle')
    if not args.bundle and (args.markdown_mode != 'classic' or args.layout_report is not None):
        parser.error('Markdown layout options require --bundle')
    try:
        options = ConversionOptions(width_scale=args.width_scale, image_storage=args.image_storage,
                                    review_images=not args.no_image_review, repeat_distance=args.repeat_distance,
                                    markdown_mode=args.markdown_mode)
        if args.bundle:
            if args.layout_report is not None:
                args.layout_report = new_path(args.layout_report)
                protect_source(args.source, args.layout_report)
                if not args.layout_report.parent.is_dir():
                    raise ValueError('layout report parent directory must exist')
            result = export_note(args.source,args.output,name=args.name,options=options,
                                 layout_debug=args.layout_report is not None)
            if args.layout_report is not None:
                with args.layout_report.open('x', encoding='utf-8') as stream:
                    json.dump(result.layout_report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        else: result = convert_pdf(args.source,args.output,options=options)
    except (ValueError,OSError) as error:
        parser.exit(1,f'Conversion failed: {error}\n')
    print(f'{result.pdf}: {result.counts}')
    if result.markdown: print(f'Markdown: {result.markdown}')
    for warning in result.warnings: print(f'Warning: {warning}')
