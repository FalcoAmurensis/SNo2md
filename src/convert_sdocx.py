#!/usr/bin/env python3
"""Compatibility entry point: same CLI and convert(source, destination, width_scale)."""
from sno2md import ConversionOptions, convert_pdf
from sno2md.model import Unsupported
from sno2md.cli import main


def convert(source, destination, width_scale=0.5):
    return convert_pdf(source, destination, options=ConversionOptions(width_scale)).counts


if __name__ == '__main__':
    main()
