"""Offline checks for the optional Kubernetes workload-health observability stack (#136)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.repo_root import repo_root


_REPO = repo_root()
_OBS = _REPO / "k8s" / "observability"
_OPS_PROM = _REPO / "ops" / "prometheus" / "prometheus.yml"
_OPS_DASH = _REPO / "ops" / "grafana" / "dashboards" / "kubernetes-workload-health.json"
_K8S_PROM = _OBS / "prometheus.yml"
_K8S_DASH = _OBS / "grafana" / "kubernetes-workload-health.json"
_DOCS = _REPO / "docs" / "observability.md"
_APP_KUSTOMIZATION = _REPO / "k8s" / "kustomization.yaml"
_APP_DEPLOYMENTS = (
    _REPO / "k8s" / "backend.yaml",
    _REPO / "k8s" / "frontend.yaml",
    _REPO / "k8s" / "postgres.yaml",
    _REPO / "k8s" / "redis.yaml",
)
_APP_CRONJOB = _REPO / "k8s" / "backend-history-capture-cronjob.yaml"

_REQUIRED_SCRAPE_JOBS = (
    "prometheus",
    "kube-state-metrics",
    "kubernetes-nodes",
    "kubernetes-cadvisor",
)

_REQUIRED_DASHBOARD_EXPR_SNIPPETS = (
    "kube_pod_status_ready",
    "kube_pod_container_status_restarts_total",
    "kube_deployment_status_replicas_unavailable",
    "kube_deployment_spec_replicas",
    "kube_deployment_status_replicas_available",
    "container_cpu_usage_seconds_total",
    "container_memory_working_set_bytes",
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "OOMKilled",
    "Pending",
    "phase=\"Succeeded\"",
    "kube_pod_status_scheduled",
    "kube_job_failed",
    "kube_job_status_failed",
    "kube_cronjob_status_active",
    "kube_pod_container_resource_requests",
    "kube_pod_container_resource_limits",
)


def _load_all_yaml(path: Path) -> list[dict]:
    docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    return [doc for doc in docs if isinstance(doc, dict)]


def _collect_exprs(node: object, out: list[str]) -> None:
    if isinstance(node, dict):
        expr = node.get("expr")
        if isinstance(expr, str):
            out.append(expr)
        for value in node.values():
            _collect_exprs(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_exprs(item, out)


def test_observability_docs_cover_setup_and_troubleshooting() -> None:
    assert _DOCS.is_file(), f"missing {_DOCS}"
    text = _DOCS.read_text(encoding="utf-8")
    required = [
        "kube-state-metrics",
        "kubectl apply -k k8s/observability",
        "Kubernetes Workload Health",
        "probe",
        "CrashLoopBackOff",
        "OOMKilled",
        "ImagePullBackOff",
        "unavailable replicas",
        "PodScheduled",
        "CronJob",
        "port-forward",
        "30300",
        "OBSERVABILITY=1",
        "obs-verify-bad",
        "#136",
        "#28",
        "Do **not** stand up a second Prometheus",
    ]
    missing = [anchor for anchor in required if anchor not in text]
    assert missing == [], f"docs/observability.md missing anchors: {missing}"


def test_app_kustomization_does_not_include_observability() -> None:
    """Keep default deploy / k3s smoke lean; observability is opt-in."""
    text = _APP_KUSTOMIZATION.read_text(encoding="utf-8")
    assert "observability" not in text


def test_ops_and_k8s_prometheus_config_stay_in_sync() -> None:
    assert _OPS_PROM.read_text(encoding="utf-8") == _K8S_PROM.read_text(encoding="utf-8")


def test_ops_and_k8s_dashboard_stay_in_sync() -> None:
    assert _OPS_DASH.read_text(encoding="utf-8") == _K8S_DASH.read_text(encoding="utf-8")


def test_ops_and_k8s_grafana_datasource_stay_in_sync() -> None:
    ops = (
        _REPO / "ops" / "grafana" / "provisioning" / "datasources" / "prometheus.yaml"
    ).read_text(encoding="utf-8")
    k8s = (_OBS / "grafana" / "datasources.yaml").read_text(encoding="utf-8")
    assert ops == k8s


def test_ops_and_k8s_grafana_dashboard_provider_stay_in_sync() -> None:
    ops = (
        _REPO / "ops" / "grafana" / "provisioning" / "dashboards" / "dashboards.yaml"
    ).read_text(encoding="utf-8")
    k8s = (_OBS / "grafana" / "dashboards.yaml").read_text(encoding="utf-8")
    assert ops == k8s


def test_prometheus_scrape_config_avoids_node_label_cardinality() -> None:
    config = yaml.safe_load(_K8S_PROM.read_text(encoding="utf-8"))
    for job_name in ("kubernetes-nodes", "kubernetes-cadvisor"):
        job = next(item for item in config["scrape_configs"] if item["job_name"] == job_name)
        relabels = job.get("relabel_configs") or []
        assert not any(relabel.get("action") == "labelmap" for relabel in relabels), (
            f"{job_name} should not labelmap all node labels"
        )
        keeps_node = any(
            relabel.get("target_label") == "node"
            and relabel.get("source_labels") == ["__meta_kubernetes_node_name"]
            for relabel in relabels
        )
        assert keeps_node, f"{job_name} should keep only the node name label"


def test_prometheus_scrape_config_has_kubernetes_targets() -> None:
    config = yaml.safe_load(_K8S_PROM.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    jobs = {job["job_name"] for job in config.get("scrape_configs", [])}
    missing = [name for name in _REQUIRED_SCRAPE_JOBS if name not in jobs]
    assert missing == [], f"prometheus.yml missing scrape jobs: {missing}"

    cadvisor = next(
        job for job in config["scrape_configs"] if job["job_name"] == "kubernetes-cadvisor"
    )
    metric_relabels = cadvisor.get("metric_relabel_configs") or []
    keep_stock_ticker = any(
        relabel.get("action") == "keep" and relabel.get("regex") == "stock-ticker"
        for relabel in metric_relabels
    )
    assert keep_stock_ticker, "cadvisor scrape should keep namespace=stock-ticker only"


def test_observability_kustomization_lists_expected_resources() -> None:
    kust = yaml.safe_load((_OBS / "kustomization.yaml").read_text(encoding="utf-8"))
    resources = kust.get("resources") or []
    for name in (
        "kube-state-metrics.yaml",
        "prometheus-rbac.yaml",
        "prometheus.yaml",
        "grafana.yaml",
    ):
        assert name in resources, f"kustomization missing resource {name}"
        assert (_OBS / name).is_file()

    generators = kust.get("configMapGenerator") or []
    names = {gen.get("name") for gen in generators}
    assert "prometheus-config" in names
    assert "grafana-datasources" in names
    assert "grafana-dashboard-provider" in names
    assert "grafana-dashboards" in names
    # Hashed ConfigMap names must remain enabled so content changes roll pods.
    assert kust.get("generatorOptions", {}).get("disableNameSuffixHash") is not True


def test_kube_state_metrics_does_not_collect_or_rbac_secrets() -> None:
    docs = _load_all_yaml(_OBS / "kube-state-metrics.yaml")
    role = next(doc for doc in docs if doc.get("kind") == "ClusterRole")
    core_resources: set[str] = set()
    for rule in role.get("rules") or []:
        if rule.get("apiGroups") == [""]:
            core_resources.update(rule.get("resources") or [])
    assert "secrets" not in core_resources

    deploy = next(doc for doc in docs if doc.get("kind") == "Deployment")
    args = deploy["spec"]["template"]["spec"]["containers"][0].get("args") or []
    resources_arg = next((arg for arg in args if arg.startswith("--resources=")), "")
    assert resources_arg, "kube-state-metrics should pin an explicit --resources allow-list"
    assert "secrets" not in resources_arg.split("=", 1)[1].split(",")


def test_teardown_cleans_observability_before_namespace_early_return() -> None:
    script = (_REPO / "scripts" / "k8s-teardown.sh").read_text(encoding="utf-8")
    assert "delete_observability_overlay" in script
    obs_idx = script.index("delete_observability_overlay")
    early_idx = script.index('Namespace ${NAMESPACE} not found')
    assert obs_idx < early_idx, "observability cleanup must run before namespace early exit"
    assert "stock-ticker-kube-state-metrics" in script


def test_observability_manifests_are_valid_yaml_with_required_kinds() -> None:
    kinds: set[tuple[str, str]] = set()
    for path in (
        _OBS / "kube-state-metrics.yaml",
        _OBS / "prometheus-rbac.yaml",
        _OBS / "prometheus.yaml",
        _OBS / "grafana.yaml",
    ):
        for doc in _load_all_yaml(path):
            kind = doc.get("kind")
            name = (doc.get("metadata") or {}).get("name")
            assert kind and name, f"{path} has a document without kind/name"
            kinds.add((kind, name))

    required = {
        ("ServiceAccount", "kube-state-metrics"),
        ("ServiceAccount", "prometheus"),
        ("ClusterRole", "stock-ticker-kube-state-metrics"),
        ("ClusterRole", "stock-ticker-prometheus"),
        ("ClusterRoleBinding", "stock-ticker-kube-state-metrics"),
        ("ClusterRoleBinding", "stock-ticker-prometheus"),
        ("Deployment", "kube-state-metrics"),
        ("Deployment", "prometheus"),
        ("Deployment", "grafana"),
        ("Service", "kube-state-metrics"),
        ("Service", "prometheus"),
        ("Service", "grafana"),
    }
    missing = sorted(required - kinds)
    assert missing == [], f"observability manifests missing kinds: {missing}"


def test_prometheus_rbac_allows_node_proxy_scrapes() -> None:
    docs = _load_all_yaml(_OBS / "prometheus-rbac.yaml")
    role = next(doc for doc in docs if doc.get("kind") == "ClusterRole")
    rules = role.get("rules") or []
    resources = {
        resource
        for rule in rules
        for resource in (rule.get("resources") or [])
    }
    for needed in ("nodes", "nodes/metrics", "nodes/proxy", "pods"):
        assert needed in resources, f"prometheus ClusterRole missing resource {needed}"
    non_resource = {
        url
        for rule in rules
        for url in (rule.get("nonResourceURLs") or [])
    }
    assert "/metrics" in non_resource
    assert "/metrics/cadvisor" in non_resource


def test_grafana_dashboard_json_valid_and_covers_health_queries() -> None:
    dashboard = json.loads(_K8S_DASH.read_text(encoding="utf-8"))
    assert dashboard.get("title") == "Kubernetes Workload Health"
    assert dashboard.get("uid") == "stock-ticker-k8s-workload-health"
    assert isinstance(dashboard.get("panels"), list)
    assert len(dashboard["panels"]) >= 10

    exprs: list[str] = []
    _collect_exprs(dashboard, exprs)
    blob = "\n".join(exprs)
    missing = [snippet for snippet in _REQUIRED_DASHBOARD_EXPR_SNIPPETS if snippet not in blob]
    assert missing == [], f"dashboard missing query snippets: {missing}"
    assert 'namespace="stock-ticker"' in blob


def test_grafana_provisioning_points_at_prometheus_and_dashboard_files() -> None:
    datasources = yaml.safe_load((_OBS / "grafana" / "datasources.yaml").read_text(encoding="utf-8"))
    assert datasources["datasources"][0]["type"] == "prometheus"
    assert datasources["datasources"][0]["url"] == "http://prometheus:9090"
    assert datasources["datasources"][0]["uid"] == "prometheus"

    provider = yaml.safe_load((_OBS / "grafana" / "dashboards.yaml").read_text(encoding="utf-8"))
    assert provider["providers"][0]["options"]["path"] == "/var/lib/grafana/dashboards"

    grafana_docs = _load_all_yaml(_OBS / "grafana.yaml")
    deploy = next(doc for doc in grafana_docs if doc.get("kind") == "Deployment")
    mounts = {
        mount["name"]: mount["mountPath"]
        for mount in deploy["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    }
    assert mounts["datasources"] == "/etc/grafana/provisioning/datasources"
    assert mounts["dashboard-provider"] == "/etc/grafana/provisioning/dashboards"
    assert mounts["dashboards"] == "/var/lib/grafana/dashboards"


def test_app_and_observability_workloads_declare_resource_requests_and_limits() -> None:
    paths = list(_APP_DEPLOYMENTS) + [
        _OBS / "prometheus.yaml",
        _OBS / "grafana.yaml",
        _OBS / "kube-state-metrics.yaml",
    ]
    for path in paths:
        found_container_resources = False
        for doc in _load_all_yaml(path):
            if doc.get("kind") != "Deployment":
                continue
            containers = doc["spec"]["template"]["spec"]["containers"]
            for container in containers:
                resources = container.get("resources") or {}
                requests = resources.get("requests") or {}
                limits = resources.get("limits") or {}
                assert "cpu" in requests and "memory" in requests, f"{path} missing requests"
                assert "cpu" in limits and "memory" in limits, f"{path} missing limits"
                found_container_resources = True
        assert found_container_resources, f"{path} has no Deployment container resources"

    cron_docs = _load_all_yaml(_APP_CRONJOB)
    cron = next(doc for doc in cron_docs if doc.get("kind") == "CronJob")
    containers = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    assert containers, "history-capture CronJob has no containers"
    for container in containers:
        resources = container.get("resources") or {}
        requests = resources.get("requests") or {}
        limits = resources.get("limits") or {}
        assert "cpu" in requests and "memory" in requests, "CronJob missing requests"
        assert "cpu" in limits and "memory" in limits, "CronJob missing limits"


def test_deploy_script_supports_optional_observability_flag() -> None:
    script = (_REPO / "scripts" / "deploy-k3s.sh").read_text(encoding="utf-8")
    assert 'OBSERVABILITY:-0' in script or 'OBSERVABILITY:-"0"' in script
    assert "k8s/observability" in script
    assert "docs/observability.md" in script


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
def test_observability_kustomize_build_succeeds() -> None:
    result = subprocess.run(
        ["kubectl", "kustomize", str(_OBS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = list(yaml.safe_load_all(result.stdout))
    kinds = {
        (doc.get("kind"), (doc.get("metadata") or {}).get("name"))
        for doc in rendered
        if isinstance(doc, dict)
    }
    assert ("Deployment", "prometheus") in kinds
    assert ("Deployment", "grafana") in kinds
    assert ("Deployment", "kube-state-metrics") in kinds
    # Hashed ConfigMap names change with content; only require the prefix.
    assert any(
        kind == "ConfigMap" and isinstance(name, str) and name.startswith("prometheus-config")
        for kind, name in kinds
    )
    assert any(
        kind == "ConfigMap" and isinstance(name, str) and name.startswith("grafana-dashboards")
        for kind, name in kinds
    )
    rendered_text = result.stdout
    # Deployments must reference the hashed ConfigMap names after kustomize build.
    assert "prometheus-config-" in rendered_text
    assert "grafana-dashboards-" in rendered_text