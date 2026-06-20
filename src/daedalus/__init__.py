"""daedalus — unified Allen-Bradley / EtherNet-IP library."""

from importlib.metadata import PackageNotFoundError, version

from daedalus.tag import Tag

try:
    __version__: str = version("daedalus")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["Tag", "__version__"]
