"""Package reload and plugin-export helpers for Sublime's module loader."""

import importlib
import sys


def _package_modules(package_name, preserved_modules=()):
    prefix = package_name + "."
    preserved = {__name__, *preserved_modules}
    return [
        name for name in tuple(sys.modules)
        if name.startswith(prefix) and name not in preserved
    ]


def load_package_entry(package_name, relative_module, preserved_modules=()):
    """Reload package internals, then import the module Sublime should inspect."""
    if not package_name:
        raise RuntimeError("Echo must be loaded as a Sublime package")
    for name in sorted(
        _package_modules(package_name, preserved_modules),
        key=lambda value: value.count("."),
        reverse=True,
    ):
        sys.modules.pop(name, None)
    return importlib.import_module("{}.{}".format(package_name, relative_module))


def export_plugin_types(entry_module, modules=None):
    """Expose Echo command/listener types from every loaded package module."""
    package_prefix = entry_module.__package__.split(".", 1)[0] + "."
    candidates = modules
    if candidates is None:
        candidates = [
            module for name, module in tuple(sys.modules.items())
            if name.startswith(package_prefix) and module is not None
        ]
    exported = {}
    for module in candidates:
        for name, value in vars(module).items():
            if (
                name.startswith("Echo")
                and isinstance(value, type)
                and getattr(value, "__module__", "").startswith(package_prefix)
            ):
                exported[name] = value
    return exported
