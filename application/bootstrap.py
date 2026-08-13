"""Plugin entry loading and type export helpers for Sublime Text."""

import importlib
import sys


def load_package_entry(package_name, relative_module, preserved_modules=()):
    """Load or refresh the application entry without dismantling its packages.

    Sublime owns package unloading. Removing nested modules here while its
    custom loader is importing ``echo.echo`` can leave parent packages in a
    half-loaded state, making valid descendants impossible to resolve.
    ``preserved_modules`` remains accepted for entry-point compatibility.
    """
    if not package_name:
        raise RuntimeError("Echo must be loaded as a Sublime package")
    del preserved_modules
    module_name = "{}.{}".format(package_name, relative_module)
    loaded = sys.modules.get(module_name)
    return (
        importlib.reload(loaded)
        if loaded is not None
        else importlib.import_module(module_name)
    )


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
