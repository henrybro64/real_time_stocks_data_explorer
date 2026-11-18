"""Stage D First-Pass CronJob activation packaging (#362).

Covers default-disabled git state, deploy preserve/restore, schedule CronJob
stays suspended, and Stage D operator script CONFIRM gating — without touching
a live cluster.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[4]
_CRON = _ROOT / "k8s" / "backend-tw-research-first-pass-drain-cronjob.yaml"
_SCHEDULE = _ROOT / "k8s" / "backend-tw-research-schedule-cronjob.yaml"
_DEPLOY = _ROOT / "scripts" / "deploy-k3s.sh"
_STAGE_D = _ROOT / "scripts" / "tw-first-pass-cronjob-stage-d.sh"
_STAGE_D_DOC = _ROOT / "docs" / "tw-research-first-pass-cronjob-stage-d-362.md"

_FP = "stock-ticker-tw-research-first-pass-drain"
_SCHED = "stock-ticker-tw-research-schedule"
_ANN = "stock-ticker.ai/first-pass-drain-desired-suspend"


def _extract_deploy_funcs(*names: str) -> str:
    script = _DEPLOY.read_text(encoding="utf-8")
    funcs = []
    for name in names:
        match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", script, re.S | re.M)
        assert match, name
        funcs.append(match.group(0))
    return "\n".join(funcs)


def _write_fake_kubectl(tmp_path: Path, initial: dict) -> Path:
    state = tmp_path / "cron_state.json"
    state.write_text(json.dumps(initial), encoding="utf-8")
    helper = tmp_path / "fake_kubectl.py"
    helper.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import sys
            from pathlib import Path

            STATE = Path(sys.argv[1])
            ANN = sys.argv[2]
            args = sys.argv[3:]

            def load():
                return json.loads(STATE.read_text(encoding="utf-8"))

            def save(data):
                STATE.write_text(json.dumps(data, indent=2), encoding="utf-8")

            def cron_name(argv):
                names = []
                take = False
                for a in argv:
                    if a == "cronjob":
                        take = True
                        continue
                    if take:
                        if a.startswith("-"):
                            break
                        names.append(a)
                return names[0] if names else None

            if not args:
                sys.exit(99)

            cmd = next((a for a in args if a in {"get", "patch", "annotate"}), None)
            name = cron_name(args)
            data = load()

            if cmd == "get":
                if name not in data:
                    print(
                        f'Error from server (NotFound): cronjobs.batch "{name}" not found',
                        file=sys.stderr,
                    )
                    sys.exit(1)
                obj = data[name]
                joined = " ".join(args)
                if "jsonpath={.spec.suspend}" in joined:
                    print(obj["suspend"])
                    sys.exit(0)
                if "go-template=" in joined:
                    print(obj.get("desired", ""))
                    sys.exit(0)
                print(obj["suspend"])
                sys.exit(0)

            if cmd == "patch":
                if name not in data:
                    sys.exit(1)
                patch_raw = None
                for i, a in enumerate(args):
                    if a == "-p" and i + 1 < len(args):
                        patch_raw = args[i + 1]
                patch = json.loads(patch_raw or "{}")
                susp = patch.get("spec", {}).get("suspend")
                if isinstance(susp, bool):
                    data[name]["suspend"] = "true" if susp else "false"
                elif susp is not None:
                    data[name]["suspend"] = str(susp).lower()
                save(data)
                print(f"patched {name} suspend={data[name]['suspend']}")
                sys.exit(0)

            if cmd == "annotate":
                if name not in data:
                    sys.exit(1)
                desired = None
                prefix = ANN + "="
                for a in args:
                    if a.startswith(prefix):
                        desired = a[len(prefix) :]
                if desired is None:
                    sys.exit(1)
                data[name]["desired"] = desired
                save(data)
                print(f"annotated {name} desired={desired}")
                sys.exit(0)

            print("unsupported:", args, file=sys.stderr)
            sys.exit(99)
            """
        ),
        encoding="utf-8",
    )
    helper.chmod(0o755)
    fake = tmp_path / "kubectl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"exec python3 '{helper}' '{state}' '{_ANN}' \"$@\"\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _base_env(fake: Path) -> dict[str, str]:
    return {
        **os.environ,
        "KUBECTL_CMD": str(fake),
        "NAMESPACE": "stock-ticker",
        "FIRST_PASS_DRAIN_CRONJOB_NAME": _FP,
        "TW_RESEARCH_SCHEDULE_CRONJOB_NAME": _SCHED,
        "FIRST_PASS_DRAIN_DESIRED_SUSPEND_ANNOTATION": _ANN,
    }


def _bool_lines(stdout: str) -> list[str]:
    return [ln for ln in stdout.strip().splitlines() if ln in {"true", "false"}]


def test_git_first_pass_cronjob_remains_default_disabled() -> None:
    text = _CRON.read_text(encoding="utf-8")
    cron = yaml.safe_load(text)
    assert cron["spec"]["suspend"] is True
    assert cron["spec"]["concurrencyPolicy"] == "Forbid"
    assert "FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND" in text or "Stage D" in text


def test_schedule_cronjob_remains_suspended() -> None:
    sched = yaml.safe_load(_SCHEDULE.read_text(encoding="utf-8"))
    assert sched["spec"]["suspend"] is True


def test_deploy_script_wires_capture_restore_and_schedule_guard() -> None:
    script = _DEPLOY.read_text(encoding="utf-8")
    assert "capture_first_pass_drain_cron_suspend" in script
    assert "restore_first_pass_drain_cron_suspend" in script
    assert "ensure_tw_research_schedule_cronjob_suspended" in script
    assert "FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND" in script
    deploy = script.split("deploy_manifests() {", 1)[1].split("\nneeds_docker()", 1)[0]
    assert deploy.index("capture_first_pass_drain_cron_suspend") < deploy.index("apply -k")
    assert deploy.index("apply -k") < deploy.index("restore_first_pass_drain_cron_suspend")
    assert deploy.index("restore_first_pass_drain_cron_suspend") < deploy.index(
        "ensure_tw_research_schedule_cronjob_suspended"
    )


def test_stage_d_docs_and_script_exist() -> None:
    assert _STAGE_D_DOC.is_file()
    assert _STAGE_D.is_file()
    text = _STAGE_D.read_text(encoding="utf-8")
    assert "CONFIRM=YES" in text
    assert "enable" in text
    assert "rollback" in text
    assert _SCHED in text
    doc = _STAGE_D_DOC.read_text(encoding="utf-8")
    assert "FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND" in doc
    assert "Does not close Issue #362" in doc


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_initial_deploy_keeps_disabled_when_cron_missing(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        {_SCHED: {"suspend": "true", "desired": ""}},
    )
    # Post-apply: git creates First-Pass CronJob as suspended.
    state = tmp_path / "cron_state.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    data[_FP] = {"suspend": "true", "desired": ""}
    state.write_text(json.dumps(data), encoding="utf-8")

    funcs = _extract_deploy_funcs(
        "read_cronjob_suspend",
        "read_cronjob_desired_suspend_annotation",
        "patch_cronjob_suspend",
        "annotate_first_pass_drain_desired_suspend",
        "restore_first_pass_drain_cron_suspend",
        "ensure_tw_research_schedule_cronjob_suspended",
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + funcs
        + "\nFIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE=missing\n"
        + "FIRST_PASS_DRAIN_CRON_DESIRED_BEFORE=\n"
        + "restore_first_pass_drain_cron_suspend\n"
        + "ensure_tw_research_schedule_cronjob_suspended\n"
        + f"read_cronjob_suspend {_FP}\n"
        + f"read_cronjob_suspend {_SCHED}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=True,
    )
    assert _bool_lines(result.stdout)[-2:] == ["true", "true"], result.stdout + result.stderr
    final = json.loads(state.read_text(encoding="utf-8"))
    assert final[_FP]["suspend"] == "true"
    assert final[_SCHED]["suspend"] == "true"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_redeploy_preserves_enabled_first_pass_cron(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        {
            _FP: {"suspend": "false", "desired": "false"},
            _SCHED: {"suspend": "true", "desired": ""},
        },
    )
    state = tmp_path / "cron_state.json"
    funcs = _extract_deploy_funcs(
        "read_cronjob_suspend",
        "read_cronjob_desired_suspend_annotation",
        "capture_first_pass_drain_cron_suspend",
        "patch_cronjob_suspend",
        "annotate_first_pass_drain_desired_suspend",
        "restore_first_pass_drain_cron_suspend",
        "ensure_tw_research_schedule_cronjob_suspended",
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + funcs
        + "\ncapture_first_pass_drain_cron_suspend\n"
        + "python3 - <<'PY'\n"
        + "import json\n"
        + f"p={str(state)!r}\n"
        + "d=json.load(open(p))\n"
        + f"d[{_FP!r}]['suspend']='true'\n"
        + "json.dump(d, open(p,'w'), indent=2)\n"
        + "PY\n"
        + "restore_first_pass_drain_cron_suspend\n"
        + "ensure_tw_research_schedule_cronjob_suspended\n"
        + f"read_cronjob_suspend {_FP}\n"
        + f"read_cronjob_suspend {_SCHED}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=True,
    )
    assert _bool_lines(result.stdout)[-2:] == ["false", "true"], result.stdout + result.stderr
    final = json.loads(state.read_text(encoding="utf-8"))
    assert final[_FP]["suspend"] == "false"
    assert final[_FP]["desired"] == "false"
    assert final[_SCHED]["suspend"] == "true"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_redeploy_keeps_disabled_first_pass_cron(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        {
            _FP: {"suspend": "true", "desired": "true"},
            _SCHED: {"suspend": "true", "desired": ""},
        },
    )
    funcs = _extract_deploy_funcs(
        "read_cronjob_suspend",
        "read_cronjob_desired_suspend_annotation",
        "capture_first_pass_drain_cron_suspend",
        "patch_cronjob_suspend",
        "annotate_first_pass_drain_desired_suspend",
        "restore_first_pass_drain_cron_suspend",
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + funcs
        + "\ncapture_first_pass_drain_cron_suspend\n"
        + "restore_first_pass_drain_cron_suspend\n"
        + f"read_cronjob_suspend {_FP}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=True,
    )
    assert _bool_lines(result.stdout)[-1] == "true"
    final = json.loads((tmp_path / "cron_state.json").read_text(encoding="utf-8"))
    assert final[_FP]["suspend"] == "true"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_force_flag_rollbacks_enabled_cron_on_deploy(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        {
            _FP: {"suspend": "false", "desired": "false"},
            _SCHED: {"suspend": "true", "desired": ""},
        },
    )
    funcs = _extract_deploy_funcs(
        "read_cronjob_suspend",
        "read_cronjob_desired_suspend_annotation",
        "capture_first_pass_drain_cron_suspend",
        "patch_cronjob_suspend",
        "annotate_first_pass_drain_desired_suspend",
        "restore_first_pass_drain_cron_suspend",
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + funcs
        + "\ncapture_first_pass_drain_cron_suspend\n"
        + "restore_first_pass_drain_cron_suspend\n"
        + f"read_cronjob_suspend {_FP}\n",
        encoding="utf-8",
    )
    env = _base_env(fake)
    env["FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND"] = "1"
    result = subprocess.run(
        ["bash", str(harness)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert _bool_lines(result.stdout)[-1] == "true"
    final = json.loads((tmp_path / "cron_state.json").read_text(encoding="utf-8"))
    assert final[_FP]["suspend"] == "true"
    assert final[_FP]["desired"] == "true"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_schedule_cron_is_forced_back_to_suspended(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        {
            _FP: {"suspend": "true", "desired": "true"},
            _SCHED: {"suspend": "false", "desired": ""},
        },
    )
    funcs = _extract_deploy_funcs(
        "read_cronjob_suspend",
        "patch_cronjob_suspend",
        "ensure_tw_research_schedule_cronjob_suspended",
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + funcs
        + "\nensure_tw_research_schedule_cronjob_suspended\n"
        + f"read_cronjob_suspend {_SCHED}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=True,
    )
    assert _bool_lines(result.stdout)[-1] == "true"
    final = json.loads((tmp_path / "cron_state.json").read_text(encoding="utf-8"))
    assert final[_SCHED]["suspend"] == "true"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_stage_d_script_requires_confirm_for_enable_and_rollback(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        {
            _FP: {"suspend": "true", "desired": "true"},
            _SCHED: {"suspend": "true", "desired": ""},
        },
    )
    denied = subprocess.run(
        ["bash", str(_STAGE_D), "enable", "k3s"],
        env={**_base_env(fake), "CONFIRM": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert denied.returncode == 2
    assert "CONFIRM=YES" in denied.stderr

    enabled = subprocess.run(
        ["bash", str(_STAGE_D), "enable", "k3s"],
        env={**_base_env(fake), "CONFIRM": "YES"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "suspend=false" in enabled.stdout
    state = json.loads((tmp_path / "cron_state.json").read_text(encoding="utf-8"))
    assert state[_FP]["suspend"] == "false"
    assert state[_SCHED]["suspend"] == "true"

    rolled = subprocess.run(
        ["bash", str(_STAGE_D), "rollback", "k3s"],
        env={**_base_env(fake), "CONFIRM": "YES"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "suspend=true" in rolled.stdout or "SUSPEND" in rolled.stdout
    state = json.loads((tmp_path / "cron_state.json").read_text(encoding="utf-8"))
    assert state[_FP]["suspend"] == "true"
    assert state[_FP]["desired"] == "true"
