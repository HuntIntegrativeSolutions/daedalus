"""daedalus — unified Allen-Bradley / EtherNet-IP library."""

from importlib.metadata import PackageNotFoundError, version

from daedalus.runtime.write_policy import InMemorySink, WriteMode, WritePolicy, WriteRecord
from daedalus.tag import Tag, TagInfo

try:
    __version__: str = version("daedalus")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "InMemorySink",
    "Tag",
    "TagInfo",
    "WriteMode",
    "WritePolicy",
    "WriteRecord",
    "__version__",
]
