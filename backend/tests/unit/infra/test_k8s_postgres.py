"""Postgres Deployment safety and the scoped AppArmor workaround (#308)."""

from __future__ import annotations

from pathlib import Path

import yaml

_K8S = Path(__file__).resolve().parents[4] / "k8s"
_ROOT = _K8S.parent


def _docs(manifest: str) -> dict[tuple[str, str], dict]:
    docs = yaml.safe_load_all((_K8S / manifest).read_text(encoding="utf-8"))
    return {(doc["kind"], doc["metadata"]["name"]): doc for doc in docs if doc}


def _postgres_deployment() -> dict:
    return _docs("postgres.yaml")[("Deployment", "stock-ticker-postgres")]


def _postgres_container() -> dict:
    containers = _postgres_deployment()["spec"]["template"]["spec"]["containers"]
    assert [item["name"] for item in containers] == ["postgres"]
    return containers[0]


def _pod_specs() -> list[tuple[str, dict]]:
    """Every pod spec in k8s/, including CronJob templates and observability."""
    found: list[tuple[str, dict]] = []
    for path in sorted(_K8S.rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not doc or "kind" not in doc:
                continue
            spec = doc.get("spec") or {}
            if doc["kind"] == "CronJob":
                spec = spec.get("jobTemplate", {}).get("spec", {})
            pod = spec.get("template", {}).get("spec")
            if pod:
                found.append((f"{path.relative_to(_K8S)}:{doc['metadata']['name']}", pod))
    return found


def test_only_one_postgres_can_run_against_the_volume() -> None:
    deployment = _postgres_deployment()
    assert deployment["spec"]["replicas"] == 1
    # Recreate stops the old pod before the new one starts. RWO alone does not
    # stop two pods on one node from mounting the same local-path volume.
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}


def test_postgres_data_volume_is_unchanged() -> None:
    # PVC spec is immutable once bound; any edit here fails the apply or risks data.
    claim = _docs("postgres.yaml")[("PersistentVolumeClaim", "stock-ticker-postgres-data")]
    assert claim["spec"] == {
        "accessModes": ["ReadWriteOnce"],
        "storageClassName": "local-path",
        "resources": {"requests": {"storage": "2Gi"}},
    }
    pod = _postgres_deployment()["spec"]["template"]["spec"]
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["data"]["persistentVolumeClaim"]["claimName"] == "stock-ticker-postgres-data"
    container = _postgres_container()
    assert {mount["name"]: mount["mountPath"] for mount in container["volumeMounts"]} == {
        "data": "/var/lib/postgresql/data"
    }
    env = {item["name"]: item["value"] for item in container["env"]}
    assert env["PGDATA"] == "/var/lib/postgresql/data/pgdata"
    # A major-version change needs pg_upgrade; never let it slip in with a redeploy.
    assert container["image"] == "postgres:16-alpine"


def test_postgres_container_security_context() -> None:
    # Unconfined is the #308 workaround. Never an explicit RuntimeDefault: kubelet
    # rejects that on a node without AppArmor (see docs/postgres-apparmor.md).
    assert _postgres_container()["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "appArmorProfile": {"type": "Unconfined"},
    }


def test_postgres_readiness_asks_postgres_and_other_probes_stay_tcp() -> None:
    container = _postgres_container()
    readiness = container["readinessProbe"]
    # No shell: nothing to expand, and the probe cannot hang in a sh wrapper.
    assert readiness["exec"]["command"] == ["pg_isready", "-q", "-U", "postgres", "-d", "postgres"]
    assert readiness["timeoutSeconds"] > 1
    # TCP startup gates readiness until the real server listens, so the
    # socket-only initdb server on first boot never counts as Ready.
    assert container["startupProbe"]["tcpSocket"]["port"] == "postgres"
    # A liveness restart during a long crash recovery would make it worse.
    assert container["livenessProbe"]["tcpSocket"]["port"] == "postgres"
    # The probe's user must exist; it is the one the Secret creates.
    secret = _docs("postgres.yaml")[("Secret", "stock-ticker-postgres")]
    assert secret["stringData"]["POSTGRES_USER"] == "postgres"


def _policy() -> dict:
    return _docs("postgres-networkpolicy.yaml")[("NetworkPolicy", "stock-ticker-postgres-ingress")]


def test_only_backend_and_worker_may_connect_to_postgres() -> None:
    policy = _policy()["spec"]
    assert policy["podSelector"] == {"matchLabels": {"app": "stock-ticker-postgres"}}
    assert policy["policyTypes"] == ["Ingress"]  # egress unchanged
    [rule] = policy["ingress"]
    assert rule["ports"] == [{"protocol": "TCP", "port": 5432}]
    [peer] = rule["from"]
    assert set(peer) == {"podSelector"}  # same namespace only, no ipBlock
    [expression] = peer["podSelector"]["matchExpressions"]
    assert expression["key"] == "app" and expression["operator"] == "In"
    assert sorted(expression["values"]) == ["stock-ticker-backend", "stock-ticker-backend-worker"]


def test_every_pod_that_loads_the_database_secret_is_allowed() -> None:
    allowed = set(_policy()["spec"]["ingress"][0]["from"][0]["podSelector"]["matchExpressions"][0]["values"])
    clients: set[str] = set()
    for path in sorted(_K8S.rglob("*.yaml")):
        if path.name == "backend-database-secret.yaml":
            continue
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if doc and "stock-ticker-backend-database" in yaml.safe_dump(doc):
                spec = doc["spec"]
                if doc["kind"] == "CronJob":
                    spec = spec["jobTemplate"]["spec"]
                clients.add(spec["template"]["metadata"]["labels"]["app"])
    assert clients == {"stock-ticker-backend", "stock-ticker-backend-worker"}
    assert clients <= allowed


def test_network_policy_is_applied_and_namespaced_by_kustomize() -> None:
    kustomization = yaml.safe_load((_K8S / "kustomization.yaml").read_text(encoding="utf-8"))
    assert "postgres-networkpolicy.yaml" in kustomization["resources"]
    assert "namespace" not in _policy()["metadata"]  # kustomize sets stock-ticker


def test_postgres_pod_security_context_is_unchanged() -> None:
    # Pod-level Unconfined would also cover any future sidecar or init container.
    assert _postgres_deployment()["spec"]["template"]["spec"]["securityContext"] == {"fsGroup": 70}


def test_unconfined_is_scoped_to_postgres_only() -> None:
    unconfined: list[str] = []
    for where, pod in _pod_specs():
        if (pod.get("securityContext") or {}).get("appArmorProfile"):
            unconfined.append(f"{where} (pod)")
        for kind in ("containers", "initContainers"):
            for item in pod.get(kind) or []:
                profile = (item.get("securityContext") or {}).get("appArmorProfile")
                if profile:
                    unconfined.append(f"{where}/{item['name']}={profile.get('type')}")
    assert unconfined == ["postgres.yaml:stock-ticker-postgres/postgres=Unconfined"]


def test_no_deprecated_apparmor_annotations() -> None:
    for path in sorted(_K8S.rglob("*.yaml")):
        assert "apparmor.security.beta.kubernetes.io" not in path.read_text(encoding="utf-8"), path


def test_postgres_is_not_otherwise_privileged() -> None:
    pod = _postgres_deployment()["spec"]["template"]["spec"]
    for key in ("hostPID", "hostIPC", "hostNetwork"):
        assert not pod.get(key), key
    context = _postgres_container()["securityContext"]
    assert "privileged" not in context
    assert "capabilities" not in context


def test_postgres_is_in_the_applied_kustomization() -> None:
    kustomization = yaml.safe_load((_K8S / "kustomization.yaml").read_text(encoding="utf-8"))
    assert "postgres.yaml" in kustomization["resources"]


def test_migrate_db_waits_for_postgres_before_alembic() -> None:
    deployment = _docs("backend.yaml")[("Deployment", "stock-ticker-backend")]
    init = {item["name"]: item for item in deployment["spec"]["template"]["spec"]["initContainers"]}
    command = init["migrate-db"]["command"]
    assert command[:2] == ["sh", "-c"]
    # && so a timed-out wait never runs Alembic against a missing database.
    assert command[2] == "python -m scripts.wait_for_database && alembic upgrade head"
    assert (_ROOT / "backend" / "scripts" / "wait_for_database.py").is_file()


def test_runbook_keeps_evidence_removal_criteria_and_rollback() -> None:
    text = (_ROOT / "docs" / "postgres-apparmor.md").read_text(encoding="utf-8")
    for needle in (
        "containerd#14485",
        "## Removal criteria",
        "### Root cause and confidence",
        "## Recovery",
        "## Rollback",
        "## Validation",
        "Never disable AppArmor globally",
        "spending-tracker",
        "--force --grace-period=0",
    ):
        assert needle in text, needle
    manifest = (_K8S / "postgres.yaml").read_text(encoding="utf-8")
    assert "docs/postgres-apparmor.md" in manifest
