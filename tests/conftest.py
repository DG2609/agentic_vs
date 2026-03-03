"""
Shared fixtures for all tests.
"""
import os
import sys
import tempfile
import shutil
import pytest

# Ensure project root is on sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope="session", autouse=True)
def set_workspace(tmp_path_factory):
    """Point WORKSPACE_DIR at a temp directory for all tests."""
    import config
    ws = str(tmp_path_factory.mktemp("workspace"))
    config.WORKSPACE_DIR = ws
    os.makedirs(ws, exist_ok=True)
    return ws


@pytest.fixture
def workspace(set_workspace):
    """Return the test workspace path."""
    return set_workspace


@pytest.fixture
def sample_py_file(workspace):
    """Create a simple Python file in workspace and return its path."""
    code = '''
def add(a: int, b: int) -> int:
    """Return sum of a and b."""
    return a + b


def factorial(n: int) -> int:
    if n <= 1:
        return 1
    return n * factorial(n - 1)


class Calculator:
    def multiply(self, x, y):
        # TODO: add input validation
        return x * y
'''
    path = os.path.join(workspace, "sample.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


@pytest.fixture
def sample_js_file(workspace):
    """Create a simple JS file in workspace."""
    code = '''
function greet(name) {
    if (!name) {
        return "Hello, World!";
    }
    return `Hello, ${name}!`;
}

const fetchData = async (url) => {
    // TODO: add error handling
    const resp = await fetch(url);
    return resp.json();
};
'''
    path = os.path.join(workspace, "sample.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path
