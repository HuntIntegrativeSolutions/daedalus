"""daedalus — unified Allen-Bradley / EtherNet-IP library."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("daedalus")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]
