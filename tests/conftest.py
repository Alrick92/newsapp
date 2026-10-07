import sys
import time
from email.utils import formatdate
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURES = Path(__file__).parent / "fixtures"
NOW = 1_790_000_000.0  # fixed clock so window maths is deterministic


def render_fixture(name: str, now: float = NOW) -> bytes:
    text = (FIXTURES / name).read_text()
    text = text.replace("{RECENT}", formatdate(now - 2 * 3600, usegmt=True))
    text = text.replace("{OLD}", formatdate(now - 7 * 86400, usegmt=True))
    text = text.replace("{RECENT_ISO}", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3 * 3600)))
    return text.encode()


@pytest.fixture
def rss_bytes() -> bytes:
    return render_fixture("rss.xml")


@pytest.fixture
def atom_bytes() -> bytes:
    return render_fixture("atom.xml")
