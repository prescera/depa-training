"""CCR release manifests (``ccr/releases/<release_id>.json``, produced by the release pipeline).

Shape: ``{release: <spec §6 record>, deployment: {image, cce_policy_b64?, resources: {cpu,
memory_gb}, env_allowlist: [...]}}``. Loading validates that the deployment really is the
measured release (pinned image digest; CCE policy hash in ``host_data`` on ``aci-snp``; image
digest in ``image_digests`` on ``gcp-cs-tdx``), so a submitter can never deploy something that
key release would reject anyway, or worse, something that differs from what was reviewed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from depa.core.encoding import b64_decode, loads_object
from depa.core.ids import FAMILIES, check_platform
from depa.errors import ValidationError

_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/:-]*@(sha256:[0-9a-f]{64})$")
_RELEASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
MAX_MANIFEST_BYTES = 4 << 20


def _fail(message: str) -> ValidationError:
    return ValidationError(f"release manifest: {message}", code="bad_release_manifest")


def _positive_number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        raise _fail(f"{what} must be a positive number")
    return float(value)


@dataclass(frozen=True)
class ReleaseManifest:
    release: dict[str, Any]
    release_id: str
    platform: str
    families: tuple[str, ...]
    tiers: tuple[int, ...]
    measurements: dict[str, Any]
    image: str
    image_digest: str
    cce_policy_b64: str | None
    cpu: float
    memory_gb: float
    env_allowlist: tuple[str, ...]

    def check_env(self, env: Mapping[str, str]) -> None:
        """Every variable the job sets must be allowlisted (the CCE policy pins the env names)."""
        extra = sorted(set(env) - set(self.env_allowlist))
        if extra:
            raise _fail(f"env not allowed by the release: {', '.join(extra)}")


def parse_release_manifest(obj: object) -> ReleaseManifest:
    if not isinstance(obj, dict) or set(obj) != {"release", "deployment"}:
        raise _fail("must be an object with exactly 'release' and 'deployment'")
    release, deployment = obj["release"], obj["deployment"]
    if not isinstance(release, dict) or not isinstance(deployment, dict):
        raise _fail("'release' and 'deployment' must be objects")

    release_id = release.get("release_id")
    if not isinstance(release_id, str) or not _RELEASE_ID_RE.fullmatch(release_id):
        raise _fail("invalid release_id")
    try:
        platform = check_platform(release.get("platform"))
    except ValidationError as exc:
        raise _fail(str(exc)) from exc
    families = release.get("families")
    if not isinstance(families, list) or not families or any(f not in FAMILIES for f in families):
        raise _fail(f"families must be a non-empty subset of {list(FAMILIES)}")
    tiers = release.get("tiers")
    if not isinstance(tiers, list) or not tiers or any(isinstance(t, bool) or t not in (1, 2) for t in tiers):
        raise _fail("tiers must be a non-empty subset of [1, 2]")
    measurements = release.get("measurements")
    if not isinstance(measurements, dict) or not measurements:
        raise _fail("measurements missing")

    image = deployment.get("image")
    if not isinstance(image, str) or (match := _IMAGE_RE.fullmatch(image)) is None:
        raise _fail("deployment.image must be digest-pinned (<repo>@sha256:<64 hex>)")
    image_digest = match.group(1)
    provenance = release.get("provenance")
    if provenance is not None:
        if not isinstance(provenance, dict):
            raise _fail("provenance must be an object")
        if "image" in provenance and provenance["image"] != image:
            raise _fail("deployment.image differs from release.provenance.image")

    cce_policy_b64 = deployment.get("cce_policy_b64")
    if cce_policy_b64 is not None and not isinstance(cce_policy_b64, str):
        raise _fail("cce_policy_b64 must be a string")
    if platform == "aci-snp":
        if not cce_policy_b64:
            raise _fail("aci-snp deployments require cce_policy_b64")
        host_data = measurements.get("host_data")
        if not isinstance(host_data, list) or not all(isinstance(h, str) and _HEX64_RE.fullmatch(h) for h in host_data):
            raise _fail("aci-snp measurements.host_data must be a list of lowercase sha256 hex")
        try:
            policy = b64_decode(cce_policy_b64)
        except ValidationError as exc:
            raise _fail("cce_policy_b64 is not valid base64") from exc
        if hashlib.sha256(policy).hexdigest() not in host_data:
            raise _fail("sha256(CCE policy) is not in measurements.host_data")
    elif platform == "gcp-cs-tdx":
        digests = measurements.get("image_digests")
        if not isinstance(digests, list) or image_digest not in digests:
            raise _fail("image digest is not in measurements.image_digests")
        if cce_policy_b64 is not None:
            raise _fail("cce_policy_b64 is only meaningful on aci-snp")

    resources = deployment.get("resources")
    if not isinstance(resources, dict):
        raise _fail("deployment.resources missing")
    cpu = _positive_number(resources.get("cpu"), "resources.cpu")
    memory_gb = _positive_number(resources.get("memory_gb"), "resources.memory_gb")
    allowlist = deployment.get("env_allowlist")
    if not isinstance(allowlist, list) or not all(isinstance(n, str) and _ENV_NAME_RE.fullmatch(n) for n in allowlist):
        raise _fail("deployment.env_allowlist must be a list of environment variable names")

    return ReleaseManifest(
        release=release,
        release_id=release_id,
        platform=platform,
        families=tuple(families),
        tiers=tuple(tiers),
        measurements=measurements,
        image=image,
        image_digest=image_digest,
        cce_policy_b64=cce_policy_b64,
        cpu=cpu,
        memory_gb=memory_gb,
        env_allowlist=tuple(allowlist),
    )


def load_release_manifest(path: Path) -> ReleaseManifest:
    data = Path(path).read_bytes()
    if len(data) > MAX_MANIFEST_BYTES:
        raise _fail("file too large")
    return parse_release_manifest(loads_object(data))
