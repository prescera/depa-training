"""Job submitters: release manifests, AKS VN2 job rendering, GCP Confidential Space argv, local runs."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from depa.deploy import DeployError, RunResult, aks, gcp, local
from depa.deploy.release import load_release_manifest, parse_release_manifest
from depa.deploy.spec import JobSpec
from depa.errors import ValidationError

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("DEPA_UPDATE_GOLDEN") == "1"

CONTRACT_ID = "0b8f6c1e-2f4a-4d7e-9a51-3c2d1e0f9a8b"
RECIPE_DIGEST = "sha256:" + "ab" * 32
IMAGE_DIGEST = "sha256:" + "cd" * 32
IMAGE = f"ispirt.azurecr.io/depa-ccr-torch@{IMAGE_DIGEST}"
POLICY = b'package policy\napi_version := "0.10.0"\n'
POLICY_B64 = base64.b64encode(POLICY).decode()
ENV_ALLOWLIST = [
    "DEPA_CONTRACT_ID",
    "DEPA_RECIPE_DIGEST",
    "DEPA_CS_URL",
    "DEPA_KMS_URL",
    "DEPA_PLATFORM",
    "DEPA_TRUST_BUNDLE",
    "DEPA_LOG_LEVEL",
]


def aci_manifest_obj() -> dict[str, Any]:
    return {
        "release": {
            "release_id": "depa-ccr-torch-2.0.0-aci",
            "platform": "aci-snp",
            "families": ["torch"],
            "tiers": [1, 2],
            "measurements": {
                "host_data": [hashlib.sha256(POLICY).hexdigest()],
                "uvm": {
                    "did": "did:x509:0:sha256:abc::eku:1.3.6.1.4.1.311.76.59.1.2",
                    "feed": "ContainerPlat-AMD-UVM",
                    "min_svn": "101",
                },
            },
            "provenance": {
                "source": "https://github.com/iSPIRT/depa-training",
                "commit": "deadbeef",
                "image": IMAGE,
            },
        },
        "deployment": {
            "image": IMAGE,
            "cce_policy_b64": POLICY_B64,
            "resources": {"cpu": 4, "memory_gb": 16},
            "env_allowlist": ENV_ALLOWLIST,
        },
    }


def gcp_manifest_obj() -> dict[str, Any]:
    gcp_image = f"asia-south1-docker.pkg.dev/ispirt/depa/depa-ccr-torch@{IMAGE_DIGEST}"
    return {
        "release": {
            "release_id": "depa-ccr-torch-2.0.0-gcp",
            "platform": "gcp-cs-tdx",
            "families": ["torch"],
            "tiers": [1, 2],
            "measurements": {
                "image_digests": [IMAGE_DIGEST],
                "support_attributes": ["STABLE"],
                "require_debug_disabled": True,
            },
            "provenance": {"image": gcp_image},
        },
        "deployment": {
            "image": gcp_image,
            "resources": {"cpu": 4, "memory_gb": 16},
            "env_allowlist": ENV_ALLOWLIST,
        },
    }


def spec(platform: str = "aci-snp", **kw: Any) -> JobSpec:
    args: dict[str, Any] = {
        "contract_id": CONTRACT_ID,
        "recipe_digest": RECIPE_DIGEST,
        "cs_url": "https://cs.example.org/app",
        "kms_url": "https://kms.example.org/app",
        "platform": platform,
        "max_duration_minutes": 120,
        "trust_bundle": "eyJ2IjoxfQ==",
        "name": "depa-ccr-0b8f6c1e-00112233",
    }
    args.update(kw)
    return JobSpec(**args)


class FakeRunner:
    def __init__(self, results: Sequence[RunResult] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.results = list(results or [])

    def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> RunResult:
        self.calls.append({"argv": list(argv), "stdin": stdin, "env": dict(env) if env is not None else None})
        return self.results.pop(0) if self.results else RunResult(0, b"", b"")


def check_golden(name: str, content: str) -> None:
    path = GOLDEN / name
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    assert path.read_text() == content, f"golden mismatch for {name} (set DEPA_UPDATE_GOLDEN=1 to regenerate)"


# ---- release manifests ------------------------------------------------------------------------


def test_release_manifest_loads(tmp_path: Path) -> None:
    p = tmp_path / "rel.json"
    p.write_text(json.dumps(aci_manifest_obj()))
    m = load_release_manifest(p)
    assert m.release_id == "depa-ccr-torch-2.0.0-aci"
    assert m.image_digest == IMAGE_DIGEST
    assert m.cpu == 4.0 and m.memory_gb == 16.0
    assert parse_release_manifest(gcp_manifest_obj()).platform == "gcp-cs-tdx"


def _mutated(base: dict[str, Any], path: Sequence[str], value: Any) -> dict[str, Any]:
    obj = copy.deepcopy(base)
    target = obj
    for key in path[:-1]:
        target = target[key]
    if value is _DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return obj


_DELETE = object()


@pytest.mark.parametrize(
    ("base", "path", "value"),
    [
        (aci_manifest_obj, ["deployment", "image"], "ispirt.azurecr.io/depa-ccr-torch:2.0.0"),
        (aci_manifest_obj, ["deployment", "image"], "ispirt.azurecr.io/depa-ccr-torch@sha256:abc"),
        (aci_manifest_obj, ["release", "measurements", "host_data"], ["00" * 32]),
        (aci_manifest_obj, ["deployment", "cce_policy_b64"], _DELETE),
        (aci_manifest_obj, ["deployment", "cce_policy_b64"], "!!!not base64"),
        (aci_manifest_obj, ["release", "provenance", "image"], f"other/depa-ccr-torch@{IMAGE_DIGEST}"),
        (aci_manifest_obj, ["release", "platform"], "azure-sgx"),
        (aci_manifest_obj, ["release", "families"], ["torch", "rust"]),
        (aci_manifest_obj, ["release", "tiers"], [3]),
        (aci_manifest_obj, ["release", "release_id"], "Bad Release"),
        (aci_manifest_obj, ["deployment", "resources", "cpu"], 0),
        (aci_manifest_obj, ["deployment", "env_allowlist"], ["lower_case"]),
        (gcp_manifest_obj, ["release", "measurements", "image_digests"], ["sha256:" + "00" * 32]),
        (gcp_manifest_obj, ["deployment", "cce_policy_b64"], POLICY_B64),
    ],
)
def test_release_manifest_rejects(base: Any, path: Sequence[str], value: Any) -> None:
    with pytest.raises(ValidationError):
        parse_release_manifest(_mutated(base(), path, value))


def test_release_manifest_rejects_extra_top_level() -> None:
    obj = aci_manifest_obj()
    obj["extra"] = {}
    with pytest.raises(ValidationError):
        parse_release_manifest(obj)


# ---- job spec ---------------------------------------------------------------------------------


def test_job_spec_env_exact() -> None:
    s = spec()
    assert list(s.env()) == [
        "DEPA_CONTRACT_ID",
        "DEPA_RECIPE_DIGEST",
        "DEPA_CS_URL",
        "DEPA_KMS_URL",
        "DEPA_PLATFORM",
        "DEPA_TRUST_BUNDLE",
        "DEPA_LOG_LEVEL",
    ]
    assert "DEPA_TRUST_BUNDLE" not in spec(trust_bundle=None).env()


def test_job_spec_names() -> None:
    s = spec(name=None)
    named = s.ensure_name()
    assert named.name is not None and named.name.startswith("depa-ccr-0b8f6c1e-") and len(named.name) == 26
    assert named.ensure_name() is named


@pytest.mark.parametrize(
    "kw",
    [
        {"contract_id": "not-a-uuid"},
        {"recipe_digest": "sha256:xyz"},
        {"cs_url": "http://cs.example.org"},
        {"kms_url": "https://kms.example.org/?q=1"},
        {"platform": "sgx"},
        {"max_duration_minutes": 0},
        {"max_duration_minutes": True},
        {"log_level": "trace"},
        {"name": "Bad_Name"},
        {"trust_bundle": "line1\nline2"},
    ],
)
def test_job_spec_rejects(kw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        spec(**kw)


# ---- AKS --------------------------------------------------------------------------------------


def test_aks_render_golden() -> None:
    m = parse_release_manifest(aci_manifest_obj())
    text = aks.render_job_yaml(spec(), m, namespace="depa", contract_max_duration_minutes=240)
    check_golden("aks_vn2_job.yaml", text)
    job = yaml.safe_load(text)
    pod = job["spec"]["template"]["spec"]
    assert job["spec"]["activeDeadlineSeconds"] == 7200
    assert job["spec"]["backoffLimit"] == 0
    assert pod["restartPolicy"] == "Never"
    assert pod["nodeSelector"] == {"type": "virtual-kubelet"}
    assert pod["tolerations"] == [{"key": "virtual-kubelet.io/provider", "operator": "Exists", "effect": "NoSchedule"}]
    annotations = job["spec"]["template"]["metadata"]["annotations"]
    assert annotations["microsoft.containerinstance.virtualnode.ccepolicy"] == POLICY_B64
    assert annotations["microsoft.containerinstance.virtualnode.injectkubeproxy"] == "false"
    assert annotations["microsoft.containerinstance.virtualnode.injectdns"] == "false"
    (container,) = pod["containers"]
    assert container["name"] == "ccr" and container["command"] == ["depa-ccr", "run"]
    assert container["image"] == IMAGE and container["workingDir"] == "/depa"
    assert container["resources"]["limits"] == {"cpu": "4", "memory": "16Gi"}
    assert "volumes" not in pod and "volumeMounts" not in container


def test_aks_yaml_injection_stays_scalar() -> None:
    m = parse_release_manifest(aci_manifest_obj())
    evil = 'x"\n        privileged: true\n      volumes: [{name: h, hostPath: {path: /}}]'
    with pytest.raises(ValidationError):  # control characters are refused up front
        spec(trust_bundle=evil)
    sneaky = 'abc", "name": "INJECTED", "value": "1'
    job = aks.render_job(spec(trust_bundle=sneaky), m, namespace="depa", contract_max_duration_minutes=240)
    env = job["spec"]["template"]["spec"]["containers"][0]["env"]
    assert {"name": "DEPA_TRUST_BUNDLE", "value": sneaky} in env
    assert all(e["name"] != "INJECTED" for e in env)


def test_aks_rejects_wrong_platform_and_duration() -> None:
    m = parse_release_manifest(aci_manifest_obj())
    with pytest.raises(ValidationError):
        aks.render_job(spec(platform="gcp-cs-tdx"), m, namespace="depa", contract_max_duration_minutes=240)
    with pytest.raises(ValidationError):
        aks.render_job(spec(), m, namespace="depa", contract_max_duration_minutes=60)
    with pytest.raises(ValidationError):
        aks.render_job(spec(), m, namespace="Bad NS", contract_max_duration_minutes=240)
    with pytest.raises(ValidationError):
        aks.render_job(spec(name=None), m, namespace="depa", contract_max_duration_minutes=240)


def test_aks_rejects_env_outside_allowlist() -> None:
    obj = aci_manifest_obj()
    obj["deployment"]["env_allowlist"] = [n for n in ENV_ALLOWLIST if n != "DEPA_TRUST_BUNDLE"]
    m = parse_release_manifest(obj)
    with pytest.raises(ValidationError):
        aks.render_job(spec(), m, namespace="depa", contract_max_duration_minutes=240)
    aks.render_job(spec(trust_bundle=None), m, namespace="depa", contract_max_duration_minutes=240)


def test_aks_submit_status_logs() -> None:
    m = parse_release_manifest(aci_manifest_obj())
    runner = FakeRunner()
    name = aks.submit(spec(), m, namespace="depa", contract_max_duration_minutes=240, context="aks-prod", runner=runner)
    assert name == "depa-ccr-0b8f6c1e-00112233"
    call = runner.calls[0]
    assert call["argv"] == ["kubectl", "--context", "aks-prod", "--namespace", "depa", "create", "-f", "-"]
    assert yaml.safe_load(call["stdin"])["metadata"]["name"] == name

    job_json = {
        "metadata": {"name": name},
        "status": {
            "failed": 1,
            "startTime": "2026-11-23T10:00:00Z",
            "conditions": [{"type": "Failed", "status": "True", "reason": "DeadlineExceeded", "message": "deadline"}],
        },
    }
    runner = FakeRunner([RunResult(0, json.dumps(job_json).encode(), b"")])
    st = aks.status(name, namespace="depa", runner=runner)
    assert st["state"] == "failed" and st["conditions"][0]["reason"] == "DeadlineExceeded"
    assert runner.calls[0]["argv"] == ["kubectl", "--namespace", "depa", "get", "job", name, "-o", "json"]

    runner = FakeRunner([RunResult(0, b'{"event":"run.started"}\n', b"")])
    assert "run.started" in aks.logs(name, namespace="depa", runner=runner)
    assert runner.calls[0]["argv"][-3:] == ["logs", f"job/{name}", "-c", "ccr"][-3:]

    with pytest.raises(DeployError):
        aks.submit(
            spec(),
            m,
            namespace="depa",
            contract_max_duration_minutes=240,
            runner=FakeRunner([RunResult(1, b"", b"forbidden")]),
        )


def test_aks_summary_states() -> None:
    assert aks.summarize_job({"status": {"succeeded": 1}})["state"] == "succeeded"
    assert aks.summarize_job({"status": {"active": 1}})["state"] == "running"
    assert aks.summarize_job({})["state"] == "pending"


# ---- GCP --------------------------------------------------------------------------------------


def test_gcp_argv_golden() -> None:
    m = parse_release_manifest(gcp_manifest_obj())
    argv = gcp.build_create_argv(
        spec(platform="gcp-cs-tdx"),
        m,
        project="depa-ccr-prod",
        zone="asia-south1-a",
        service_account="ccr-runner@depa-ccr-prod.iam.gserviceaccount.com",
        contract_max_duration_minutes=240,
    )
    check_golden("gcp_create_argv.json", json.dumps(argv, indent=2) + "\n")
    assert "--confidential-compute-type=TDX" in argv
    assert "--max-run-duration=7200s" in argv and "--instance-termination-action=DELETE" in argv
    assert "--image-family=confidential-space" in argv and "--boot-disk-type=pd-balanced" in argv
    assert not any(a.startswith("--min-cpu-platform") for a in argv)
    metadata = next(a for a in argv if a.startswith("--metadata="))
    items = dict(kv.split("=", 1) for kv in metadata.removeprefix("--metadata=^~^").split("~"))
    assert items["tee-image-reference"] == m.image
    assert items["tee-restart-policy"] == "Never"
    assert items["tee-container-log-redirect"] == "false"
    assert items["tee-env-DEPA_CONTRACT_ID"] == CONTRACT_ID
    assert items["tee-env-DEPA_TRUST_BUNDLE"] == "eyJ2IjoxfQ=="


def test_gcp_debug_and_rejections() -> None:
    m = parse_release_manifest(gcp_manifest_obj())
    common: dict[str, Any] = {
        "project": "depa-ccr-prod",
        "zone": "asia-south1-a",
        "service_account": "ccr-runner@depa-ccr-prod.iam.gserviceaccount.com",
        "contract_max_duration_minutes": 240,
    }
    argv = gcp.build_create_argv(spec(platform="gcp-cs-tdx"), m, debug=True, **common)
    assert "--image-family=confidential-space-debug" in argv
    assert any("tee-container-log-redirect=true" in a for a in argv)
    with pytest.raises(ValidationError):
        gcp.build_create_argv(spec(platform="gcp-cs-tdx", trust_bundle="a~tee-env-X=1"), m, **common)
    with pytest.raises(ValidationError):
        gcp.build_create_argv(spec(platform="gcp-cs-tdx"), m, **{**common, "machine_type": "n2d-standard-4"})
    with pytest.raises(ValidationError):
        gcp.build_create_argv(spec(platform="aci-snp"), m, **common)
    with pytest.raises(ValidationError):
        gcp.build_create_argv(spec(platform="gcp-cs-tdx"), m, **{**common, "project": "--impersonate"})


def test_gcp_submit_status_logs() -> None:
    m = parse_release_manifest(gcp_manifest_obj())
    runner = FakeRunner()
    name = gcp.submit(
        spec(platform="gcp-cs-tdx", name=None),
        m,
        project="depa-ccr-prod",
        zone="asia-south1-a",
        service_account="ccr-runner@depa-ccr-prod.iam.gserviceaccount.com",
        contract_max_duration_minutes=240,
        runner=runner,
    )
    assert runner.calls[0]["argv"][4] == name and name.startswith("depa-ccr-0b8f6c1e-")
    describe = {
        "name": name,
        "status": "RUNNING",
        "confidentialInstanceConfig": {"confidentialInstanceType": "TDX"},
    }
    st = gcp.status(
        name,
        project="depa-ccr-prod",
        zone="asia-south1-a",
        runner=FakeRunner([RunResult(0, json.dumps(describe).encode(), b"")]),
    )
    assert st["state"] == "running" and st["confidential_type"] == "TDX"
    gone = gcp.status(
        name,
        project="depa-ccr-prod",
        zone="asia-south1-a",
        runner=FakeRunner([RunResult(1, b"", b"ERROR: The resource 'x' was not found")]),
    )
    assert gone["state"] == "deleted"
    with pytest.raises(DeployError):
        gcp.status(
            name,
            project="depa-ccr-prod",
            zone="asia-south1-a",
            runner=FakeRunner([RunResult(1, b"", b"permission denied")]),
        )
    runner = FakeRunner([RunResult(0, b"serial", b"")])
    assert gcp.logs(name, project="depa-ccr-prod", zone="asia-south1-a", runner=runner) == "serial"
    assert runner.calls[0]["argv"][3] == "get-serial-port-output"


# ---- local ------------------------------------------------------------------------------------


def test_local_subprocess_run() -> None:
    runner = FakeRunner([RunResult(0, b"ok", b"")])
    job = local.run_local(spec(platform="virtual"), image=None, runner=runner, env_extra={"EXTRA": "1"})
    assert job.succeeded
    call = runner.calls[0]
    assert call["argv"][1:] == ["-m", "depa.ccr", "run"]
    env = call["env"]
    assert env["DEPA_PLATFORM"] == "virtual" and env["EXTRA"] == "1"
    assert set(env) <= {"PATH", "HOME", "EXTRA", *spec(platform="virtual").env()}


def test_local_docker_run() -> None:
    runner = FakeRunner([RunResult(3, b"", b"failed")])
    job = local.run_local(spec(platform="virtual"), image="depa2-sdk-ccr-classic:dev", runner=runner)
    assert job.returncode == 3 and job.name == "depa2-sdk-ccr-0b8f6c1e-00112233"
    argv = runner.calls[0]["argv"]
    assert argv[:7] == ["docker", "run", "--rm", "--name", job.name, "--network", "host"]
    assert argv[-3:] == ["depa2-sdk-ccr-classic:dev", "depa-ccr", "run"]
    env_args = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert all(e.startswith("DEPA_") for e in env_args) and len(env_args) == 7


def test_local_rejections() -> None:
    with pytest.raises(ValidationError):
        local.run_local(spec(platform="aci-snp"), image=None, runner=FakeRunner())
    with pytest.raises(ValidationError):
        local.run_local(spec(platform="virtual"), image="--privileged", runner=FakeRunner())
    with pytest.raises(ValidationError):
        local.run_local(spec(platform="virtual"), image="img:dev", runner=FakeRunner(), env_extra={"X": "1"})
    with pytest.raises(ValidationError):
        local.status("someone-elses-container", runner=FakeRunner())


def test_local_status_logs() -> None:
    inspect = [{"State": {"Status": "exited", "ExitCode": 0, "StartedAt": "a", "FinishedAt": "b"}}]
    st = local.status("depa2-sdk-ccr-abc", runner=FakeRunner([RunResult(0, json.dumps(inspect).encode(), b"")]))
    assert st["state"] == "exited" and st["exit_code"] == 0
    assert (
        local.status("depa2-sdk-ccr-abc", runner=FakeRunner([RunResult(1, b"", b"Error: No such container")]))["state"]
        == "gone"
    )
    assert local.logs("depa2-sdk-ccr-abc", runner=FakeRunner([RunResult(0, b"out\n", b"err\n")])) == "out\nerr\n"
