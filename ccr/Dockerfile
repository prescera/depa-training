# syntax=docker/dockerfile:1.12
#
# DEPA Training v2 clean-room (CCR) images, one per trainer family. Build from the repo root:
#
#   docker buildx build -f ccr/Dockerfile --target classic -t depa-ccr-classic .
#   (or ccr/tools/build-release.sh, which also produces the release manifest)
#
# Targets: classic | torch | llm. Each image contains
#   /opt/depa/agent    venv: depa SDK, provides `depa-ccr` (the attested runtime agent, runs as root)
#   /opt/depa/trainer  venv: depa-train + the family's hash-locked stack; executed by the agent
#                      as uid/gid 10001 with a scrubbed environment (spec §12 step 4). Its entry
#                      point is published to the agent as DEPA_CCR_TRAINER.
#
# Container contract shared with the SDK job builders and the CCE policy template: one
# container `ccr`, command ["depa-ccr","run"], work dir /depa, only the DEPA_* runtime
# variables are operator-supplied, no privileged mode, no volume mounts.
#
# Reproducibility: base pinned by digest; every Python dependency installed with
# --require-hashes --no-deps; in-repo packages built with a hash-locked backend and no build
# isolation; bytecode compiled with unchecked-hash invalidation (no embedded mtimes); pass
# SOURCE_DATE_EPOCH and export with rewrite-timestamp=true for stable layer timestamps.

ARG PYTHON_BASE=docker.io/library/python:3.12-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# ---------------------------------------------------------------------------------------------
FROM ${PYTHON_BASE} AS builder
ARG SOURCE_DATE_EPOCH=0
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PIP_NO_CACHE_DIR=1 \
    PIP_NO_COMPILE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1
COPY --chmod=0755 ccr/build/install-venv.sh /usr/local/bin/install-venv

# Wheels of the in-repo packages (no network access to unpinned build backends).
FROM builder AS wheels
COPY ccr/requirements/build.txt /tmp/build.txt
RUN python -m venv /opt/build \
 && /opt/build/bin/pip install --require-hashes --no-deps -r /tmp/build.txt
COPY sdk/python /src/sdk/python
COPY trainers /src/trainers
RUN /opt/build/bin/pip wheel --no-deps --no-build-isolation --wheel-dir /wheels \
      /src/sdk/python /src/trainers

FROM builder AS agent
COPY ccr/requirements/agent.txt /tmp/agent.txt
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    install-venv /opt/depa/agent /tmp/agent.txt /wheels/depa-*.whl /wheels/depa_train-*.whl

# classic: dp-xgboost 0.2.10 has no cp312 wheel and is built from its hashed sdist, which needs
# a C++ toolchain and cmake. The toolchain never leaves this stage; only the venv is copied.
FROM builder AS trainer-classic
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ make cmake \
 && rm -rf /var/lib/apt/lists/*
ENV CMAKE_POLICY_VERSION_MINIMUM=3.5
COPY trainers/requirements/classic.txt /tmp/trainer.txt
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    install-venv /opt/depa/trainer /tmp/trainer.txt /wheels/depa_train-*.whl

FROM builder AS trainer-torch
COPY trainers/requirements/torch.txt /tmp/trainer.txt
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    install-venv /opt/depa/trainer /tmp/trainer.txt /wheels/depa_train-*.whl

FROM builder AS trainer-llm
COPY trainers/requirements/llm.txt /tmp/trainer.txt
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    install-venv /opt/depa/trainer /tmp/trainer.txt /wheels/depa_train-*.whl

# ---------------------------------------------------------------------------------------------
FROM ${PYTHON_BASE} AS runtime
# Trainer identity: no home, no login shell. Strip package managers, pip and setuid/setgid bits
# from the runtime filesystem (dpkg status is kept so SBOM scanners can enumerate OS packages).
RUN groupadd --system --gid 10001 trainer \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin trainer \
 && python -m pip uninstall -y -q pip \
 && rm -rf /usr/local/bin/pip* /usr/local/bin/idle* /usr/local/bin/pydoc* \
      /usr/local/lib/python3.12/ensurepip /usr/local/lib/python3.12/idlelib \
      /usr/local/lib/python3.12/tkinter /usr/local/lib/python3.12/turtledemo \
      /usr/bin/apt* /usr/bin/dpkg* /usr/lib/apt /etc/apt /var/cache/apt /var/lib/apt \
 && find / -xdev -type f -perm /6000 -exec chmod a-s {} + \
 && mkdir -m 0755 /depa
COPY --from=agent /opt/depa/agent /opt/depa/agent
ENV PATH=/opt/depa/agent/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEPA_CCR_TRAINER=/opt/depa/trainer/bin/depa-train
WORKDIR /depa
# GCP Confidential Space launch policy (gcp-cs-tdx): only the spec §12 runtime variables may be
# overridden by the operator, the command may not, and container logs leave the VM only when
# running the debug image.
LABEL tee.launch_policy.allow_env_override="DEPA_CONTRACT_ID,DEPA_RECIPE_DIGEST,DEPA_CS_URL,DEPA_KMS_URL,DEPA_PLATFORM,DEPA_TRUST_BUNDLE,DEPA_LOG_LEVEL" \
      tee.launch_policy.allow_cmd_override="false" \
      tee.launch_policy.log_redirect="debugonly" \
      org.opencontainers.image.source="https://github.com/prescera/depa-training" \
      org.opencontainers.image.licenses="CC0-1.0"
ENTRYPOINT ["depa-ccr", "run"]

# ---------------------------------------------------------------------------------------------
FROM runtime AS classic
COPY --from=trainer-classic /opt/depa/trainer /opt/depa/trainer
LABEL org.opencontainers.image.title="depa-ccr-classic" in.ispirt.depa.family="classic"

FROM runtime AS torch
COPY --from=trainer-torch /opt/depa/trainer /opt/depa/trainer
LABEL org.opencontainers.image.title="depa-ccr-torch" in.ispirt.depa.family="torch"

FROM runtime AS llm
COPY --from=trainer-llm /opt/depa/trainer /opt/depa/trainer
LABEL org.opencontainers.image.title="depa-ccr-llm" in.ispirt.depa.family="llm"
