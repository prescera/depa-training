"""Safe resolution of input files and the list of serialization formats that are never read."""

from __future__ import annotations

from pathlib import Path

from ..errors import SafetyError

# Formats that can execute code when deserialized. They are never read, whatever the plugin.
FORBIDDEN_SUFFIXES = frozenset(
    {".pkl", ".pickle", ".pt", ".pth", ".ckpt", ".bin", ".joblib", ".npy", ".npz", ".dill", ".cloudpickle"}
)
MAX_MATCHES = 1_000_000


def resolve_files(root: Path, pattern: str, *, suffixes: frozenset[str] | None = None) -> list[Path]:
    """Return regular files under ``root`` matching ``pattern``, sorted, with containment checks.

    Rejects symlinks, parent traversal and absolute patterns. ``suffixes`` restricts the accepted
    file extensions (lower-cased, including the dot; ``.nii.gz`` style double suffixes allowed).
    """
    if pattern.startswith("/") or ".." in Path(pattern).parts or "\\" in pattern:
        raise SafetyError("file pattern must be relative and must not contain '..'")
    base = root.resolve(strict=True)
    matches: list[Path] = []
    for path in sorted(root.glob(pattern)):
        if path.is_symlink():
            raise SafetyError("symbolic links are not allowed in input datasets")
        if not path.is_file():
            continue
        real = path.resolve(strict=True)
        if not real.is_relative_to(base):
            raise SafetyError("input file resolves outside its dataset directory")
        suffix = _suffix(path)
        if suffix in FORBIDDEN_SUFFIXES:
            raise SafetyError(f"refusing to read '{suffix}' files (unsafe serialization format)")
        if suffixes is not None and suffix not in suffixes:
            raise SafetyError(
                f"file extension '{suffix}' is not accepted here (accepted: {sorted(suffixes)})"
            )
        matches.append(real)
        if len(matches) > MAX_MATCHES:
            raise SafetyError("too many files match the pattern")
    return matches


def _suffix(path: Path) -> str:
    suffixes = [s.lower() for s in path.suffixes]
    if len(suffixes) >= 2 and suffixes[-1] == ".gz":
        return "".join(suffixes[-2:])
    return suffixes[-1] if suffixes else ""


def reject_unsafe_files(root: Path) -> None:
    """Fail if a directory tree contains symlinks or files in a code-executing format."""
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SafetyError("symbolic links are not allowed in input datasets")
        if path.is_file() and _suffix(path) in FORBIDDEN_SUFFIXES:
            raise SafetyError(f"refusing to load a directory containing '{_suffix(path)}' files")
