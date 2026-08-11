"""Sublime Text entry point for the Echo package."""

import importlib
import sys


_bootstrap_name = __package__ + ".application.bootstrap"
sys.modules.pop(_bootstrap_name, None)
_bootstrap = importlib.import_module(_bootstrap_name)

_entry = _bootstrap.load_package_entry(
    __package__, "application.entry", (__name__,)
)
plugin_loaded = _entry.plugin_loaded
plugin_unloaded = _entry.plugin_unloaded
globals().update(_bootstrap.export_plugin_types(_entry))
