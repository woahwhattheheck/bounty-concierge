"""Check real Helm output for the released PayD634 backend listener change."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import yaml

EXPECTED_SOURCE = "fd4960e766ccd804ac7a0f99f788efde511a45b1"
EXPECTED_DEPLOYMENT = "c47947dda4708079e7c876b9fc632748c4fc2d9e"
EVIDENCE = Path("evidence")

def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()

source_sha = git("-C", "source", "rev-parse", "HEAD")
deployment_blob = git("-C", "source", "rev-parse", "HEAD:charts/payd/templates/backend-deployment.yaml")
assert source_sha == EXPECTED_SOURCE
assert deployment_blob == EXPECTED_DEPLOYMENT

def one(objects, kind):
    matches = [obj for obj in objects if obj.get("kind") == kind and obj.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == "backend"]
    assert len(matches) == 1, (kind, len(matches))
    return matches[0]

def verify(filename, listener):
    raw = (EVIDENCE / filename).read_bytes()
    objects = [obj for obj in yaml.safe_load_all(raw) if obj]
    deployment = one(objects, "Deployment")
    service = one(objects, "Service")
    config = one(objects, "ConfigMap")
    pod = deployment["spec"]["template"]
    containers = [c for c in pod["spec"]["containers"] if c["name"] == "backend"]
    assert len(containers) == 1, containers
    container = containers[0]
    service_ports = [p for p in service["spec"]["ports"] if p["name"] == "http"]
    assert len(service_ports) == 1, service_ports
    service_port = service_ports[0]
    assert service_port["port"] == 8080, service_port
    assert service_port["targetPort"] == "http", service_port
    listener_ports = [p for p in container["ports"] if p["name"] == "http"]
    assert len(listener_ports) == 1, listener_ports
    assert listener_ports[0]["containerPort"] == listener, listener_ports
    referenced_configs = [entry["configMapRef"]["name"] for entry in container.get("envFrom", []) if "configMapRef" in entry]
    assert referenced_configs == [config["metadata"]["name"]], referenced_configs
    assert config["data"]["PORT"] == str(listener), config["data"].get("PORT")
    selector = service["spec"]["selector"]
    labels = pod["metadata"]["labels"]
    assert selector and all(labels.get(key) == value for key, value in selector.items()), (selector, labels)
    ingress_routes = [path["backend"]["service"] for obj in objects if obj.get("kind") == "Ingress" for rule in obj.get("spec", {}).get("rules", []) for path in rule.get("http", {}).get("paths", []) if path.get("backend", {}).get("service", {}).get("name") == service["metadata"]["name"]]
    assert ingress_routes and all(route["port"].get("number") == 8080 for route in ingress_routes), ingress_routes
    probes = {}
    for name in ("startupProbe", "livenessProbe", "readinessProbe"):
        actual = container[name]["httpGet"]["port"]
        assert actual == "http", (name, actual)
        probes[name] = actual
    return {
        "case": filename.removesuffix(".yaml"),
        "result": "pass",
        "rendered_objects": len(objects),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "service": {"name": service["metadata"]["name"], "port": service_port["port"], "targetPort": service_port["targetPort"]},
        "deployment": {"name": deployment["metadata"]["name"], "containerPort": listener_ports[0]["containerPort"]},
        "config_map": {"name": config["metadata"]["name"], "PORT": config["data"]["PORT"]},
        "probe_ports": probes,
        "service_selector_matches_pod": True,
        "ingress_backend_ports": [route["port"]["number"] for route in ingress_routes],
    }

cases = [
    verify("service-8080-listener-3001.yaml", 3001),
    verify("service-8080-listener-4001.yaml", 4001),
]
receipt = {
    "schema": "payd634-listener-helm-render/v1",
    "source_repository": "woahwhattheheck/PayD",
    "source_sha": source_sha,
    "deployment_blob": deployment_blob,
    "controller_sha": git("-C", "review", "rev-parse", "HEAD"),
    "run_id": os.environ["GITHUB_RUN_ID"],
    "helm": (EVIDENCE / "helm-version.txt").read_text().strip(),
    "python": platform.python_version(),
    "pyyaml": yaml.__version__,
    "runner_os": platform.platform(),
    "values_file": "charts/payd/values-staging.yaml",
    "fixture": "Documented RENDER_ONLY_NOT_A_SIGNING_KEY file and backend Ingress path override8080; no real credential.",
    "passed": len(cases),
    "failed": 0,
    "cases": cases,
    "scope": "Two actual complete-chart renders. No Kubernetes deployment, application test suite, or production source changes.",
}
(EVIDENCE / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
print("PAYD634_HELM_RECEIPT " + json.dumps(receipt, sort_keys=True))
