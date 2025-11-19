# Make 'lib' a regular package so PyInstaller and Python can reliably import submodules
# This helps resolve imports like 'lib.data_manager_service' in frozen builds.

__all__ = []
