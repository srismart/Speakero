"""Static checks on static/index.html: every getElementById target must exist,
and the inline script must be syntactically valid JavaScript."""
import re
import shutil
import subprocess
import tempfile
import os

import pytest

HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")


def _read_html() -> str:
    with open(HTML_PATH, encoding="utf-8") as f:
        return f.read()


def test_all_dom_ids_referenced_in_js_exist():
    html = _read_html()
    defined = set(re.findall(r'id="([^"]+)"', html))
    referenced = set(re.findall(r"getElementById\('([^']+)'\)", html))
    missing = sorted(referenced - defined)
    assert not missing, f"JS references missing DOM ids: {missing}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inline_script_is_valid_javascript():
    html = _read_html()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts, "no inline <script> block found"
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        f.write("\n".join(scripts))
        path = f.name
    try:
        result = subprocess.run(
            ["node", "--check", path], capture_output=True, text=True
        )
        assert result.returncode == 0, f"node --check failed:\n{result.stderr}"
    finally:
        os.unlink(path)
