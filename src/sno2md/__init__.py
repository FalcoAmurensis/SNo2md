"""Public API; lazy imports keep the binary inspector standard-library-only."""
from .model import ConversionOptions, ConversionResult, Note, DecodeError, RenderError, OutputError, Unsupported

__version__ = '0.2.0'
__all__ = ['convert_pdf','export_note','read_note','ConversionOptions','ConversionResult',
           'Note','DecodeError','RenderError','OutputError','Unsupported']


def __getattr__(name):
    if name in ('convert_pdf','export_note'):
        from . import api
        return getattr(api,name)
    if name == 'read_note':
        from .decoder import read_note
        return read_note
    raise AttributeError(name)
