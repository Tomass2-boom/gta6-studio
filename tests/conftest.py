"""Windows capture node: make the suite run green on Windows without touching Mac-owned code or tests.

* Python on Windows reads text files as cp1252 unless told otherwise; the fixtures are UTF-8 (emoji in chat). Default
  Path.read_text/write_text to UTF-8 for the test run, like macOS does.
* A few tests exercise Mac-only parts (the review page's process runner, the renderer that only the Mac runs). They are
  skipped, with the reason, on Windows only.
"""
import pathlib
import sys

import pytest

# werkzeug builds its exception table at import time by iterating its own module globals; imported in the middle of a
# test run on Python 3.13 that fails with "dictionary changed size during iteration". Import it first, once.
try:
    import flask  # noqa: F401
except Exception:   # pragma: no cover
    pass

if sys.platform == "win32":
    _read, _write = pathlib.Path.read_text, pathlib.Path.write_text
    pathlib.Path.read_text = lambda self, encoding=None, errors=None, newline=None: _read(self, encoding or "utf-8", errors) \
        if sys.version_info < (3, 13) else _read(self, encoding or "utf-8", errors, newline)
    pathlib.Path.write_text = lambda self, data, encoding=None, errors=None, newline=None: _write(
        self, data, encoding or "utf-8", errors, newline)

WINDOWS_SKIP = {
    "tests/test_control_app.py::test_start_stop_is_ctrl_c": "Mac review runner: preexec_fn / process groups",
    "tests/test_render.py::test_render_fixture": "the renderer runs on the Mac (ffmpeg ass= path escaping is not Windows-safe)",
    "tests/test_look_upgrades.py::test_render_with_all_extras_on": "the renderer runs on the Mac (ffmpeg ass= path escaping)",
}


def pytest_collection_modifyitems(items):
    if sys.platform != "win32":
        return
    for item in items:
        for prefix, why in WINDOWS_SKIP.items():
            if item.nodeid.startswith(prefix):
                item.add_marker(pytest.mark.skip(reason=f"Windows node: {why}"))
