import subprocess
import sys

from lazytrack import __version__


def test_version():
    assert __version__ == "0.1.0-dev"


def test_package_import():
    import lazytrack
    assert hasattr(lazytrack, "__version__")


def test_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "lazytrack.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "lazytrack" in result.stdout.lower()


def test_cli_version():
    from lazytrack.cli import version
    from io import StringIO
    import contextlib
    
    buffer = StringIO()
    with contextlib.redirect_stdout(buffer):
        version()
    output = buffer.getvalue()
    assert "0.1.0-dev" in output
