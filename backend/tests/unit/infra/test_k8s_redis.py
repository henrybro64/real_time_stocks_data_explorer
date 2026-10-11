"""Redis persistence and memory cap in k8s (#293)."""

from __future__ import annotations

from pathlib import Path

import yaml

_K8S = Path(__file__).resolve().parents[4] / "k8s"


def _docs() -> dict[tuple[str, str], dict]:
    docs = yaml.safe_load_all((_K8S / "redis.yaml").read_text(encoding="utf-8"))
    return {(doc["kind"], doc["metadata"]["name"]): doc for doc in docs if doc}


def _conf() -> dict[str, list[str]]:
    text = _docs()[("ConfigMap", "stock-ticker-redis-config")]["data"]["redis.conf"]
    conf: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition(" ")
            conf.setdefault(key, []).append(value.strip())
    return conf


def test_redis_has_a_memory_cap_with_room_for_a_snapshot_fork() -> None:
    conf = _conf()
    assert conf["maxmemory-policy"] == ["noeviction"]
    assert conf["maxmemory"] == ["128mb"]
    container = _docs()[("Deployment", "stock-ticker-redis")]["spec"]["template"]["spec"][
        "containers"
    ][0]
    assert container["resources"]["limits"]["memory"] == "256Mi"  # 2x the cap


def test_redis_persists_snapshots_to_a_volume() -> None:
    conf = _conf()
    assert conf["dir"] == ["/data"]
    assert conf["save"], "RDB snapshots enabled"
    assert conf["stop-writes-on-bgsave-error"] == ["no"]
    docs = _docs()
    claim = docs[("PersistentVolumeClaim", "stock-ticker-redis-data")]
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    deployment = docs[("Deployment", "stock-ticker-redis")]
    assert deployment["spec"]["strategy"]["type"] == "Recreate"  # RWO volume
    pod = deployment["spec"]["template"]["spec"]
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["data"]["persistentVolumeClaim"]["claimName"] == "stock-ticker-redis-data"
    assert volumes["config"]["configMap"]["name"] == "stock-ticker-redis-config"
    container = pod["containers"][0]
    mounts = {mount["name"]: mount["mountPath"] for mount in container["volumeMounts"]}
    assert mounts == {"config": "/usr/local/etc/redis", "data": "/data"}
    # args (not command) so the image entrypoint still chowns /data.
    assert "command" not in container
    assert container["args"] == ["redis-server", "/usr/local/etc/redis/redis.conf"]


def test_redis_stays_reachable_inside_the_cluster() -> None:
    conf = _conf()
    assert conf["bind"] == ["0.0.0.0"]
    assert conf["protected-mode"] == ["no"]
