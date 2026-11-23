"""``depa-train`` command line interface.

Exit codes: 0 success/valid, 1 run failed, 2 invalid recipe or usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .api import validate_against_contract
from .errors import RecipeError
from .recipe import load_recipe_file, recipe_json_schema
from .registry import registry
from .validation import validate_recipe


def _plugin_schema_filename(ref: str) -> str:
    return ref.replace("/", "__") + ".json"


def _cmd_run(args: argparse.Namespace) -> int:
    from .executor import execute, write_failure

    try:
        document = load_recipe_file(args.recipe)
    except RecipeError as exc:
        write_failure(Path(args.result), f"invalid recipe: {exc}")
        return 2
    result = execute(document, args.inputs, args.outputs, args.result, install_guard=True)
    if result["status"] == "succeeded":
        return 0
    return 2 if str(result.get("error", "")).startswith("invalid recipe") else 1


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        document = load_recipe_file(args.recipe)
        errors = validate_recipe(document)
        if args.contract and not errors:
            contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
            errors = validate_against_contract(document, contract)
    except RecipeError as exc:
        errors = exc.errors
    except (OSError, json.JSONDecodeError) as exc:
        errors = [f"contract: cannot read: {exc}"]
    if args.json:
        print(json.dumps({"valid": not errors, "errors": errors}, indent=2))
    elif errors:
        for err in errors:
            print(err)
    else:
        print("valid")
    return 0 if not errors else 2


def _cmd_plugins(args: argparse.Namespace) -> int:
    plugins = registry().all()
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "ref": p.ref,
                        "version": p.version,
                        "tier": p.tier,
                        "families": sorted(p.families),
                        "experimental": p.experimental,
                        "dp": p.dp_capable,
                        "summary": p.summary,
                        "wraps": p.wraps,
                    }
                    for p in plugins
                ],
                indent=2,
            )
        )
        return 0
    for p in plugins:
        flags = ("experimental " if p.experimental else "") + ("dp" if p.dp_capable else "")
        families = ",".join(sorted(p.families))
        print(f"{p.ref:<24} {p.version:<8} tier {p.tier}  {families:<20} {flags:<16} {p.summary}")
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    if args.write:
        out = Path(args.write)
        (out / "plugins").mkdir(parents=True, exist_ok=True)
        (out / "recipe.v1.json").write_text(json.dumps(recipe_json_schema(), indent=2) + "\n")
        for p in registry().all():
            (out / "plugins" / _plugin_schema_filename(p.ref)).write_text(
                json.dumps(p.json_schema(), indent=2) + "\n"
            )
        return 0
    if args.plugin is None:
        print(json.dumps(recipe_json_schema(), indent=2))
        return 0
    ref = args.plugin if "@" in args.plugin else None
    candidates = [registry().get(ref)] if ref else registry().find(args.plugin)
    candidates = [c for c in candidates if c is not None]
    if not candidates:
        print(f"unknown plugin: {args.plugin}", file=sys.stderr)
        return 2
    plugin = max(candidates, key=lambda p: p.major)
    print(json.dumps(plugin.json_schema(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="depa-train", description="DEPA Training v2 training runtime")
    parser.add_argument("--version", action="version", version=f"depa-train {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="execute a recipe")
    run.add_argument("--recipe", required=True, help="recipe file (JSON or YAML)")
    run.add_argument(
        "--inputs", required=True, help="directory with one read-only sub-directory per input alias"
    )
    run.add_argument(
        "--outputs", required=True, help="empty directory; outputs are written to <outputs>/<name>/"
    )
    run.add_argument("--result", required=True, help="path of result.json")
    run.set_defaults(func=_cmd_run)

    val = sub.add_parser(
        "validate", help="validate a recipe (and optionally its consistency with a contract)"
    )
    val.add_argument("--recipe", required=True)
    val.add_argument("--contract", help="contract JSON (depa-training/contract/v2)")
    val.add_argument("--json", action="store_true", help="machine-readable output")
    val.set_defaults(func=_cmd_validate)

    plugins = sub.add_parser("plugins", help="list available plugins")
    plugins.add_argument("--json", action="store_true")
    plugins.set_defaults(func=_cmd_plugins)

    schema = sub.add_parser("schema", help="print the recipe JSON Schema or a plugin's 'with' schema")
    schema.add_argument("plugin", nargs="?", help="plugin name or name@major")
    schema.add_argument("--write", metavar="DIR", help="write all schemas to DIR")
    schema.set_defaults(func=_cmd_schema)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
