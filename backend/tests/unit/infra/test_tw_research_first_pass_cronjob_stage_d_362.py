"""Stage D First-Pass CronJob activation packaging (#362).

Covers default-disabled git state, deploy preserve/restore (live-only),
failed-apply restore, schedule CronJob stay suspended, Stage D CONFIRM +
enable prechecks, in-flight Job notes, and annotation normalization.
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
_SECRET = "stock-ticker-backend-auth"


def _extract_deploy_funcs(*names: str) -> str:
    script = _DEPLOY.read_text(encoding="utf-8")
    funcs = []
    for name in names:
        match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", script, re.S | re.M)
        assert match, name
        funcs.append(match.group(0))
    return "\n".join(funcs)


def _default_state(**overrides) -> dict:
    state = {
        "cronjobs": {
            _FP: {"suspend": "true", "desired": "true"},
            _SCHED: {"suspend": "true", "desired": ""},
        },
        "secret_keys": [
            "TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN",
            "TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID",
            "TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED",
        ],
        "backend_env": {
            "ENABLED": "true",
            "USER_ID": "set",
            "TOKEN": "set",
        },
        "jobs": [],
        "jobs_fail": False,
        "jobs_padding_bytes": 0,
        "apply_fail": False,
        "patch_fail": False,
        "patch_calls": 0,
    }
    state.update(overrides)
    return state


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

            def cron_names(argv):
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
                return names

            if not args:
                sys.exit(99)

            data = load()
            joined = " ".join(args)

            if "apply" in args and "-k" in args:
                if data.get("apply_fail"):
                    print("fake apply failed", file=sys.stderr)
                    sys.exit(1)
                print("fake apply ok")
                sys.exit(0)

            if "get" in args and "secret" in args:
                keys = set(data.get("secret_keys") or [])
                # go-template={{if index .data "KEY"}}present{{end}}
                for a in args:
                    if "index .data" in a or "index .data" in joined:
                        for key in (
                            "TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN",
                            "TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID",
                            "TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED",
                        ):
                            if key in a or key in joined:
                                sys.stdout.write("present" if key in keys else "")
                                sys.exit(0)
                sys.exit(0)

            if "exec" in args:
                env = data.get("backend_env") or {}
                print(f"ENABLED={env.get('ENABLED', 'unset')}")
                print(f"USER_ID={env.get('USER_ID', 'unset')}")
                print(f"TOKEN={env.get('TOKEN', 'unset')}")
                sys.exit(0)

            if "get" in args and "jobs" in args:
                if data.get("jobs_fail"):
                    print("fake get jobs failed", file=sys.stderr)
                    sys.exit(1)
                items = []
                for job in data.get("jobs") or []:
                    items.append(
                        {
                            "metadata": {
                                "name": job.get("name"),
                                "ownerReferences": [
                                    {
                                        "kind": "CronJob",
                                        "name": job.get("owner", ""),
                                    }
                                ],
                            },
                            "status": {"active": job.get("active", 0)},
                        }
                    )
                payload = {"items": items}
                pad = int(data.get("jobs_padding_bytes") or 0)
                if pad > 0:
                    payload["padding"] = "x" * pad
                print(json.dumps(payload))
                sys.exit(0)

            cmd = next((a for a in args if a in {"get", "patch", "annotate"}), None)
            names = cron_names(args)
            name = names[0] if names else None
            cronjobs = data.setdefault("cronjobs", {})

            if cmd == "get":
                if name not in cronjobs:
                    print(
                        f'Error from server (NotFound): cronjobs.batch "{name}" not found',
                        file=sys.stderr,
                    )
                    sys.exit(1)
                obj = cronjobs[name]
                if "jsonpath={.spec.suspend}" in joined:
                    print(obj["suspend"])
                    sys.exit(0)
                if "go-template=" in joined:
                    desired = obj.get("desired", "")
                    if desired == "__missing__":
                        print("<no value>")
                    else:
                        print(desired)
                    sys.exit(0)
                print(obj["suspend"])
                sys.exit(0)

            if cmd == "patch":
                data["patch_calls"] = int(data.get("patch_calls") or 0) + 1
                if data.get("patch_fail"):
                    save(data)
                    print("fake patch failed", file=sys.stderr)
                    sys.exit(1)
                if name not in cronjobs:
                    sys.exit(1)
                patch_raw = None
                for i, a in enumerate(args):
                    if a == "-p" and i + 1 < len(args):
                        patch_raw = args[i + 1]
                patch = json.loads(patch_raw or "{}")
                susp = patch.get("spec", {}).get("suspend")
                if isinstance(susp, bool):
                    cronjobs[name]["suspend"] = "true" if susp else "false"
                elif susp is not None:
                    cronjobs[name]["suspend"] = str(susp).lower()
                save(data)
                print(f"patched {name} suspend={cronjobs[name]['suspend']}")
                sys.exit(0)

            if cmd == "annotate":
                if name not in cronjobs:
                    sys.exit(1)
                desired = None
                prefix = ANN + "="
                for a in args:
                    if a.startswith(prefix):
                        desired = a[len(prefix) :]
                if desired is None:
                    sys.exit(1)
                cronjobs[name]["desired"] = desired
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
        "SECRET_NAME": _SECRET,
        "DEPLOY_NAME": "stock-ticker-backend",
    }


def _bool_lines(stdout: str) -> list[str]:
    return [ln for ln in stdout.strip().splitlines() if ln in {"true", "false"}]


def _restore_funcs() -> str:
    return _extract_deploy_funcs(
        "normalize_desired_suspend_annotation",
        "read_cronjob_suspend",
        "read_cronjob_desired_suspend_annotation",
        "capture_first_pass_drain_cron_suspend",
        "patch_cronjob_suspend",
        "annotate_first_pass_drain_desired_suspend",
        "restore_first_pass_drain_cron_suspend",
        "ensure_tw_research_schedule_cronjob_suspended",
        "apply_kustomize_preserving_first_pass_suspend",
        "first_pass_drain_interrupt_restore",
        "first_pass_drain_on_signal",
        "install_first_pass_drain_interrupt_trap",
        "clear_first_pass_drain_interrupt_trap",
    )


def test_git_first_pass_cronjob_remains_default_disabled() -> None:
    text = _CRON.read_text(encoding="utf-8")
    cron = yaml.safe_load(text)
    assert cron["spec"]["suspend"] is True
    assert cron["spec"]["concurrencyPolicy"] == "Forbid"


def test_schedule_cronjob_remains_suspended() -> None:
    sched = yaml.safe_load(_SCHEDULE.read_text(encoding="utf-8"))
    assert sched["spec"]["suspend"] is True


def test_deploy_script_wires_capture_restore_and_schedule_guard() -> None:
    script = _DEPLOY.read_text(encoding="utf-8")
    assert "capture_first_pass_drain_cron_suspend" in script
    assert "restore_first_pass_drain_cron_suspend" in script
    assert "apply_kustomize_preserving_first_pass_suspend" in script
    assert "normalize_desired_suspend_annotation" in script
    deploy = script.split("deploy_manifests() {", 1)[1].split("\nneeds_docker()", 1)[0]
    assert deploy.index("capture_first_pass_drain_cron_suspend") < deploy.index(
        "apply_kustomize_preserving_first_pass_suspend"
    )


def test_stage_d_docs_cover_observe_and_live_only_preserve() -> None:
    doc = _STAGE_D_DOC.read_text(encoding="utf-8")
    assert "Un-suspend **only** when pre-apply **live**" in doc
    assert "Observe first unattended ticks" in doc
    assert "lastSuccessfulTime" in doc
    assert "awaiting_analyst_candidate_selection" in doc
    assert "~16.7 s/symbol" in doc or "16.7" in doc
    assert "same" in doc and "Phase-2" in doc
    assert "reserve_expires_at" in doc
    assert "Does not close Issue #362" in doc


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_normalize_desired_annotation_empty_and_no_value(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(tmp_path, _default_state())
    funcs = _extract_deploy_funcs("normalize_desired_suspend_annotation")
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + funcs
        + "\nnormalize_desired_suspend_annotation ''\n"
        + "normalize_desired_suspend_annotation '<no value>'\n"
        + "normalize_desired_suspend_annotation false\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stdout.splitlines()
    assert lines[0] == ""
    assert lines[1] == ""
    assert lines[2] == "false"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_initial_deploy_keeps_disabled_when_cron_missing(tmp_path: Path) -> None:
    state_obj = _default_state()
    del state_obj["cronjobs"][_FP]
    fake = _write_fake_kubectl(tmp_path, state_obj)
    state = tmp_path / "cron_state.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    data["cronjobs"][_FP] = {"suspend": "true", "desired": ""}
    state.write_text(json.dumps(data), encoding="utf-8")

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
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


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_redeploy_preserves_live_false_even_without_annotation(tmp_path: Path) -> None:
    """(b) before=false, desired unset (plain kubectl enable) → preserved false."""
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            cronjobs={
                _FP: {"suspend": "false", "desired": ""},
                _SCHED: {"suspend": "true", "desired": ""},
            }
        ),
    )
    state = tmp_path / "cron_state.json"
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
        + "\ncapture_first_pass_drain_cron_suspend\n"
        + "python3 - <<'PY'\n"
        + "import json\n"
        + f"p={str(state)!r}\n"
        + "d=json.load(open(p))\n"
        + f"d['cronjobs'][{_FP!r}]['suspend']='true'\n"
        + "json.dump(d, open(p,'w'), indent=2)\n"
        + "PY\n"
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
    assert _bool_lines(result.stdout)[-1] == "false", result.stdout + result.stderr
    final = json.loads(state.read_text(encoding="utf-8"))
    assert final["cronjobs"][_FP]["suspend"] == "false"
    assert final["cronjobs"][_FP]["desired"] == "false"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_stale_desired_false_with_live_true_stays_suspended(tmp_path: Path) -> None:
    """(a) before=true, desired=false → stays true, annotation becomes true, warning."""
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            cronjobs={
                _FP: {"suspend": "true", "desired": "false"},
                _SCHED: {"suspend": "true", "desired": ""},
            }
        ),
    )
    state = tmp_path / "cron_state.json"
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
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
    assert "live suspend=true but desired=false" in result.stderr
    assert "leaving suspended" in result.stderr
    final = json.loads(state.read_text(encoding="utf-8"))
    assert final["cronjobs"][_FP]["suspend"] == "true"
    assert final["cronjobs"][_FP]["desired"] == "true"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_annotation_no_value_treated_as_unset_on_capture(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            cronjobs={
                _FP: {"suspend": "true", "desired": "__missing__"},
                _SCHED: {"suspend": "true", "desired": ""},
            }
        ),
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
        + "\ncapture_first_pass_drain_cron_suspend\n"
        + 'printf "desired=[%s]\\n" "${FIRST_PASS_DRAIN_CRON_DESIRED_BEFORE}"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "desired=[]" in result.stdout
    assert "<no value>" not in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_force_flag_rollbacks_enabled_cron_on_deploy(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            cronjobs={
                _FP: {"suspend": "false", "desired": "false"},
                _SCHED: {"suspend": "true", "desired": ""},
            }
        ),
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
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
    assert final["cronjobs"][_FP]["suspend"] == "true"
    assert final["cronjobs"][_FP]["desired"] == "true"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_failed_apply_restores_when_before_false(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            cronjobs={
                _FP: {"suspend": "false", "desired": "false"},
                _SCHED: {"suspend": "true", "desired": ""},
            },
            apply_fail=True,
        ),
    )
    state = tmp_path / "cron_state.json"
    # Simulate apply having rewritten suspend=true before failing mid-flight.
    data = json.loads(state.read_text(encoding="utf-8"))
    data["cronjobs"][_FP]["suspend"] = "true"
    state.write_text(json.dumps(data), encoding="utf-8")

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
        + "\nFIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE=false\n"
        + "FIRST_PASS_DRAIN_CRON_DESIRED_BEFORE=false\n"
        + "if apply_kustomize_preserving_first_pass_suspend /tmp/fake-kustomize; then\n"
        + "  echo unexpected_success; exit 1\n"
        + "fi\n"
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
    assert "First-Pass drain suspend state restored after failed apply" in result.stderr
    assert _bool_lines(result.stdout)[-1] == "false"
    final = json.loads(state.read_text(encoding="utf-8"))
    assert final["cronjobs"][_FP]["suspend"] == "false"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_failed_apply_left_suspended_when_before_true(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(apply_fail=True),
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
        + "\nFIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE=true\n"
        + "FIRST_PASS_DRAIN_CRON_DESIRED_BEFORE=true\n"
        + "if apply_kustomize_preserving_first_pass_suspend /tmp/fake-kustomize; then\n"
        + "  echo unexpected_success; exit 1\n"
        + "fi\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "left suspended — re-run enable" in result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_schedule_cron_is_forced_back_to_suspended(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            cronjobs={
                _FP: {"suspend": "true", "desired": "true"},
                _SCHED: {"suspend": "false", "desired": ""},
            }
        ),
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
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


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_stage_d_enable_requires_confirm(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(tmp_path, _default_state())
    denied = subprocess.run(
        ["bash", str(_STAGE_D), "enable", "k3s"],
        env={**_base_env(fake), "CONFIRM": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert denied.returncode == 2
    assert "CONFIRM=YES" in denied.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize(
    "override,needle",
    [
        ({"secret_keys": ["TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID", "TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED"]}, "TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN"),
        ({"backend_env": {"ENABLED": "false", "USER_ID": "set", "TOKEN": "set"}}, "ENABLED is not true"),
        ({"backend_env": {"ENABLED": "true", "USER_ID": "unset", "TOKEN": "set"}}, "USER_ID unset"),
        ({"backend_env": {"ENABLED": "true", "USER_ID": "set", "TOKEN": "unset"}}, "TOKEN unset"),
        (
            {
                "jobs": [
                    {"name": "fp-drain-1", "owner": _FP, "active": 1},
                ]
            },
            "active Job",
        ),
        (
            {
                "cronjobs": {
                    _FP: {"suspend": "true", "desired": "true"},
                    _SCHED: {"suspend": "false", "desired": ""},
                }
            },
            "schedule CronJob must be suspended",
        ),
    ],
)
def test_stage_d_enable_refuses_prechecks(tmp_path: Path, override: dict, needle: str) -> None:
    state = _default_state()
    state.update(override)
    if "cronjobs" in override:
        state["cronjobs"] = override["cronjobs"]
    fake = _write_fake_kubectl(tmp_path, state)
    result = subprocess.run(
        ["bash", str(_STAGE_D), "enable", "k3s"],
        env={**_base_env(fake), "CONFIRM": "YES"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert needle in result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_stage_d_enable_happy_path_and_rollback_notes_inflight(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(tmp_path, _default_state())
    enabled = subprocess.run(
        ["bash", str(_STAGE_D), "enable", "k3s"],
        env={**_base_env(fake), "CONFIRM": "YES"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "suspend=false" in enabled.stdout
    state = json.loads((tmp_path / "cron_state.json").read_text(encoding="utf-8"))
    assert state["cronjobs"][_FP]["suspend"] == "false"

    # Add in-flight job then rollback.
    state["jobs"] = [{"name": "fp-drain-running", "owner": _FP, "active": 1}]
    (tmp_path / "cron_state.json").write_text(json.dumps(state), encoding="utf-8")
    rolled = subprocess.run(
        ["bash", str(_STAGE_D), "rollback", "k3s"],
        env={**_base_env(fake), "CONFIRM": "YES"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert rolled.returncode == 0
    assert "in-flight drain Job fp-drain-running still running" in rolled.stdout
    assert "re-run verify" in rolled.stdout
    final = json.loads((tmp_path / "cron_state.json").read_text(encoding="utf-8"))
    assert final["cronjobs"][_FP]["suspend"] == "true"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_stage_d_status_reports_active_jobs(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            jobs=[{"name": "fp-drain-active", "owner": _FP, "active": 1}],
        ),
    )
    result = subprocess.run(
        ["bash", str(_STAGE_D), "status", "k3s"],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "active_first_pass_drain_jobs:" in result.stdout
    assert "fp-drain-active" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_enable_refuses_when_jobs_list_fails_without_patch(tmp_path: Path) -> None:
    """(a) get jobs exits 1 → enable exit 3, no patch call."""
    fake = _write_fake_kubectl(tmp_path, _default_state(jobs_fail=True))
    result = subprocess.run(
        ["bash", str(_STAGE_D), "enable", "k3s"],
        env={**_base_env(fake), "CONFIRM": "YES"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3
    assert "cannot list Jobs to verify no active First-Pass drain Job" in result.stderr
    final = json.loads((tmp_path / "cron_state.json").read_text(encoding="utf-8"))
    assert int(final.get("patch_calls") or 0) == 0
    assert final["cronjobs"][_FP]["suspend"] == "true"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_enable_refuses_large_jobs_json_with_active_owned_job(tmp_path: Path) -> None:
    """(b) >200 KB jobs JSON with one active owned Job → enable refuses and names it."""
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            jobs=[{"name": "fp-drain-huge", "owner": _FP, "active": 1}],
            jobs_padding_bytes=220_000,
        ),
    )
    result = subprocess.run(
        ["bash", str(_STAGE_D), "enable", "k3s"],
        env={**_base_env(fake), "CONFIRM": "YES"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert "fp-drain-huge" in result.stderr
    final = json.loads((tmp_path / "cron_state.json").read_text(encoding="utf-8"))
    assert int(final.get("patch_calls") or 0) == 0


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_status_prints_unknown_when_jobs_list_fails(tmp_path: Path) -> None:
    """(c) status prints unknown when Jobs cannot be listed."""
    fake = _write_fake_kubectl(tmp_path, _default_state(jobs_fail=True))
    result = subprocess.run(
        ["bash", str(_STAGE_D), "status", "k3s"],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "active_first_pass_drain_jobs: unknown (could not list Jobs)" in result.stdout

    verified = subprocess.run(
        ["bash", str(_STAGE_D), "verify", "k3s"],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 1
    assert "could not list First-Pass drain Jobs" in verified.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_sigterm_after_apply_restores_live_false(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            cronjobs={
                _FP: {"suspend": "false", "desired": "false"},
                _SCHED: {"suspend": "true", "desired": ""},
            }
        ),
    )
    state = tmp_path / "cron_state.json"
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
        + "\nFIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE=false\n"
        + "FIRST_PASS_DRAIN_CRON_DESIRED_BEFORE=false\n"
        + "install_first_pass_drain_interrupt_trap\n"
        + "python3 - <<'PY'\n"
        + "import json\n"
        + f"p={str(state)!r}\n"
        + "d=json.load(open(p))\n"
        + f"d['cronjobs'][{_FP!r}]['suspend']='true'\n"
        + "json.dump(d, open(p,'w'), indent=2)\n"
        + "PY\n"
        + "( sleep 0.2; kill -TERM $$ ) &\n"
        + "sleep 2\n"
        + "echo reached_end\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 143
    assert "reached_end" not in result.stdout
    assert "First-Pass drain restore attempted after interrupted deploy" in result.stderr
    final = json.loads(state.read_text(encoding="utf-8"))
    assert final["cronjobs"][_FP]["suspend"] == "false"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_sigint_after_apply_restores_live_false(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            cronjobs={
                _FP: {"suspend": "false", "desired": "false"},
                _SCHED: {"suspend": "true", "desired": ""},
            }
        ),
    )
    state = tmp_path / "cron_state.json"
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
        + "\nFIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE=false\n"
        + "FIRST_PASS_DRAIN_CRON_DESIRED_BEFORE=false\n"
        + "install_first_pass_drain_interrupt_trap\n"
        + "python3 - <<'PY'\n"
        + "import json\n"
        + f"p={str(state)!r}\n"
        + "d=json.load(open(p))\n"
        + f"d['cronjobs'][{_FP!r}]['suspend']='true'\n"
        + "json.dump(d, open(p,'w'), indent=2)\n"
        + "PY\n"
        + "( sleep 0.2; kill -INT $$ ) &\n"
        + "sleep 2\n"
        + "echo reached_end\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 130
    assert "reached_end" not in result.stdout
    assert "First-Pass drain restore attempted after interrupted deploy" in result.stderr
    final = json.loads(state.read_text(encoding="utf-8"))
    assert final["cronjobs"][_FP]["suspend"] == "false"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_sigterm_when_before_true_exits_without_restore(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            cronjobs={
                _FP: {"suspend": "true", "desired": "true"},
                _SCHED: {"suspend": "true", "desired": ""},
            }
        ),
    )
    state = tmp_path / "cron_state.json"
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
        + "\nFIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE=true\n"
        + "FIRST_PASS_DRAIN_CRON_DESIRED_BEFORE=true\n"
        + "install_first_pass_drain_interrupt_trap\n"
        + "( sleep 0.2; kill -TERM $$ ) &\n"
        + "sleep 2\n"
        + "echo reached_end\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 143
    assert "reached_end" not in result.stdout
    assert "First-Pass drain restore attempted after interrupted deploy" not in result.stderr
    final = json.loads(state.read_text(encoding="utf-8"))
    assert int(final.get("patch_calls") or 0) == 0


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_restore_patch_failure_prints_error(tmp_path: Path) -> None:
    fake = _write_fake_kubectl(
        tmp_path,
        _default_state(
            cronjobs={
                _FP: {"suspend": "true", "desired": "false"},
                _SCHED: {"suspend": "true", "desired": ""},
            },
            patch_fail=True,
        ),
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _restore_funcs()
        + "\nFIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE=false\n"
        + "FIRST_PASS_DRAIN_CRON_DESIRED_BEFORE=false\n"
        + "set +e\n"
        + "restore_first_pass_drain_cron_suspend\n"
        + "rc=$?\n"
        + "set -e\n"
        + "echo RC=$rc\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env=_base_env(fake),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "RC=1"
    assert "ERROR: First-Pass drain left suspended (was active)" in result.stderr
    assert "tw-first-pass-cronjob-stage-d.sh enable" in result.stderr
