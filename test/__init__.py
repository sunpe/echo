"""Echo test suite loaded through the same package namespace as Sublime."""

import sys
from pathlib import Path
from types import ModuleType


if "echo" not in sys.modules:
    package = ModuleType("echo")
    package.__path__ = [str(Path(__file__).resolve().parents[1])]
    sys.modules["echo"] = package
