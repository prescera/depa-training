"""Fake `depa-train` for tests: `run --recipe R --inputs I --outputs O --result F`.

Behaviour comes from the recipe name ``mini-<behaviour>``: ok | fail | overspend | undeclared |
badformat | sleep | escape.
"""

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    args = dict(zip(sys.argv[2::2], sys.argv[3::2], strict=False))
    recipe = json.loads(Path(args["--recipe"]).read_text())
    behaviour = recipe["name"].removeprefix("mini-")
    inputs, outputs, result = Path(args["--inputs"]), Path(args["--outputs"]), Path(args["--result"])
    for item in recipe["inputs"]:
        assert (inputs / item["alias"]).is_dir(), item
    rows = sum(1 for _ in (inputs / "loans" / "loans.csv").open()) - 1
    # Inputs must be read-only and the environment scrubbed.
    try:
        (inputs / "loans" / "loans.csv").write_text("x")
        writable = True
    except OSError:
        writable = False
    leaked = sorted(k for k in os.environ if k.startswith("DEPA_"))
    if behaviour == "fail":
        return 3
    if behaviour == "sleep":
        time.sleep(3600)
    model_dir = outputs / "model"
    model_dir.mkdir()
    if behaviour == "badformat":
        (model_dir / "model.json").write_text("not json")
    else:
        (model_dir / "model.json").write_text(json.dumps({"version": [2, 0, 0], "learner": {"rows": rows}}))
    if behaviour == "escape":
        (model_dir / "model.json.link").symlink_to("/etc/passwd")
    files = ["model.json.link"] if behaviour == "escape" else ["model.json"]
    out = [{"name": "model", "format": "xgboost-json", "files": files}]
    if behaviour == "undeclared":
        (outputs / "extra").mkdir()
        (outputs / "extra" / "extra.json").write_text("{}")
        out.append({"name": "extra", "format": "xgboost-json", "files": ["extra.json"]})
    eps = 9.0 if behaviour == "overspend" else 1.9
    result.write_text(
        json.dumps(
            {
                "status": "succeeded",
                "metrics": {
                    "accuracy": 0.9,
                    "loss": 0.1,
                    "secret": 42,
                    "rows_writable": float(writable),
                    "leak": ",".join(leaked),
                },
                "privacy_spent": {"mode": "dp", "epsilon": eps, "delta": 1e-5, "accountant": "rdp"},
                "outputs": out,
                "debug": {"inputs_writable": writable, "env": leaked},
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
