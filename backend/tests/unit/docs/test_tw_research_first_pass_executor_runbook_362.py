"""Guardrails for the First-Pass executor runbook (#362)."""

from __future__ import annotations

from tests.repo_root import repo_root


_RUNBOOK = (
    repo_root() / "docs" / "tw-research-first-pass-executor-362-366.md"
)


def test_runbook_never_documents_authorization_bearer() -> None:
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "Authorization: Bearer" not in text, (
        "Runbook must not mention Authorization: Bearer; "
        "drain uses X-Tw-Research-First-Pass-Drain-Token from inside the pod env"
    )


def test_runbook_documents_in_pod_drain_header_and_dry_run() -> None:
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "X-Tw-Research-First-Pass-Drain-Token" in text
    assert "TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN" in text
    assert '"dryRun": True' in text or '"dryRun": true' in text
    assert "127.0.0.1:8000/api/tw-research/first-pass/drain" in text
    assert "default_transaction_read_only=on" in text
    assert "maxBatches" in text and "batchSize" in text


def test_runbook_heredoc_kubectl_exec_uses_stdin_flag() -> None:
    """Heredoc stdin to kubectl exec requires ``exec -i`` (no ``-t``)."""
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "pgcrypto" not in text

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "kubectl" not in line or "exec" not in line:
            continue
        # Join physical line plus bash continuation lines (trailing \).
        command = line.rstrip()
        look = index
        while command.endswith("\\") and look + 1 < len(lines):
            look += 1
            command = command[:-1] + " " + lines[look].strip()
        if "<<'PY'" not in command and "<<'SQL'" not in command:
            continue
        assert " exec -i " in f" {command} ", (
            f"kubectl exec feeding a heredoc must use -i (stdin); got: {command!r}"
        )
        assert " -t " not in f" {command} " and not command.rstrip().endswith(" -t"), (
            f"kubectl exec heredoc must not use -t; got: {command!r}"
        )
