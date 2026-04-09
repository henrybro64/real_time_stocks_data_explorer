import subprocess
from pathlib import Path

import allure
import pytest


ROOT_DIR = Path(__file__).resolve().parents[3]
SMOKE_SCRIPT = ROOT_DIR / "scripts" / "ci-k3s-smoke.sh"

pytestmark = pytest.mark.smoke


@allure.title("k3s deployment smoke")
def test_k3s_smoke() -> None:
    result = subprocess.run(
        ["bash", str(SMOKE_SCRIPT)],
        capture_output=True,
        cwd=ROOT_DIR,
        text=True,
    )

    if result.stdout:
        allure.attach(result.stdout, name="stdout", attachment_type=allure.attachment_type.TEXT)
    if result.stderr:
        allure.attach(result.stderr, name="stderr", attachment_type=allure.attachment_type.TEXT)

    assert result.returncode == 0, f"{SMOKE_SCRIPT.name} failed with exit code {result.returncode}"
