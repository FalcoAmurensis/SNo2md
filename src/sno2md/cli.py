"""CLI shared by the package entry point and legacy script."""
import argparse
import sys
from pathlib import Path
from .api import convert_pdf, export_note
from .model import ConversionOptions


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
    args = parser.parse_args(argv)
    if not args.bundle and args.name is not None:
        parser.error('--name requires --bundle')
    try:
        options = ConversionOptions(width_scale=args.width_scale)
        if args.bundle:
            result = export_note(args.source,args.output,name=args.name,options=options)
        else: result = convert_pdf(args.source,args.output,options=options)
    except (ValueError,OSError) as error:
        parser.exit(1,f'Conversion failed: {error}\n')
    print(f'{result.pdf}: {result.counts}')
    if result.markdown: print(f'Markdown: {result.markdown}')
    for warning in result.warnings: print(f'Warning: {warning}')
