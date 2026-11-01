"""Read-only case metadata normalization for Part A.

This additive API leaves CaseContext and the agreed cross-part contracts intact.
Callers may pass workspace paths explicitly when replaying container metadata on
a host; otherwise relative task paths are interpreted relative to task.json.

It also says how the package is built (Debian or RPM) and why, so later parts
do not assume Debian for an RPM case such as the starter kit's `hello`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path


@dataclass
class PackageFormat:
    """How the package is built: "debian", "rpm" or "unknown", and why.

    `basis` names the evidence that decided: "metadata" (task.json or the case
    manifest), "files" (packaging files at the top of input/) or "none".
    `evidence` keeps every observation, including ones that disagree, so a
    conflict stays visible instead of being silently resolved.
    """
    name: str = "unknown"
    basis: str = "none"
    evidence: list[str] = field(default_factory=list)


@dataclass
class CaseInfo:
    case_id: str | None
    package: str | None
    target_arch: str | None
    worktree: Path
    log_path: Path
    task_metadata: dict
    manifest_metadata: dict
    package_format: PackageFormat = field(default_factory=PackageFormat)


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _string(*values) -> str | None:
    return next((v for v in values if isinstance(v, str) and v.strip()), None)


def _nested(data: dict, key: str, field_name: str):
    value = data.get(key)
    return value.get(field_name) if isinstance(value, dict) else None


# --- Package format -----------------------------------------------------------

_TYPE_NAMES = {"deb": "debian", "debian": "debian", "dsc": "debian",
               "rpm": "rpm", "srpm": "rpm"}
_DEBIAN_ARTIFACTS = (".deb", ".ddeb", ".udeb", ".dsc")
_RPM_ARTIFACTS = (".rpm",)
_MAX_LISTED = 5_000      # top-level entries of input/ to look at; bounded on huge trees
_MAX_NAMED = 3           # file names quoted per kind of evidence


def _metadata_votes(label: str, data: dict) -> list[tuple[str | None, str]]:
    """(format or None, reason) for each format statement in one metadata file."""
    votes: list[tuple[str | None, str]] = []
    build = data.get("build") if isinstance(data.get("build"), dict) else {}

    declared = _string(data.get("package_type"), data.get("package_format"))
    if declared:
        name = _TYPE_NAMES.get(declared.strip().lower())
        votes.append((name, f"{label} package_type is {declared!r}"
                      + ("" if name else " (not a recognized format)")))

    recipe = _string(build.get("recipe"))
    if recipe:
        lower = recipe.lower()
        if lower.endswith(".dsc"):
            votes.append(("debian", f"{label} build recipe {recipe} is a Debian source description"))
        elif lower.endswith(".spec"):
            votes.append(("rpm", f"{label} build recipe {recipe} is an RPM spec file"))

    backend = _string(build.get("backend"))
    if backend:
        lower = backend.lower()
        if "rpm" in lower:
            votes.append(("rpm", f"{label} build backend is {backend!r}"))
        elif "deb" in lower:
            votes.append(("debian", f"{label} build backend is {backend!r}"))

    artifacts = data.get("expected_artifacts")
    if isinstance(artifacts, dict):
        patterns = [p for value in artifacts.values() if isinstance(value, list)
                    for p in value if isinstance(p, str)]
        for name, suffixes in (("debian", _DEBIAN_ARTIFACTS), ("rpm", _RPM_ARTIFACTS)):
            matching = [p for p in patterns if p.lower().endswith(suffixes)]
            if matching:
                votes.append((name, f"{label} expects {', '.join(matching[:_MAX_NAMED])}"))
    return votes


def _top_level_names(input_dir: Path) -> list[str] | None:
    """File names directly in input/, at most _MAX_LISTED; None if unreadable."""
    try:
        with os.scandir(input_dir) as entries:
            names = []
            for entry in entries:
                if len(names) >= _MAX_LISTED:
                    break
                names.append(entry.name)
    except OSError:
        return None
    return sorted(names)


def _quote(names: list[str]) -> str:
    shown = ", ".join(f"input/{n}" for n in names[:_MAX_NAMED])
    return shown + (f" and {len(names) - _MAX_NAMED} more" if len(names) > _MAX_NAMED else "")


def _from_files(worktree: Path) -> PackageFormat:
    """Infer the format from packaging files at the top of input/.

    Only the top level is examined. Upstream projects often ship their own
    .spec file (18 of the 200 public Debian cases do, e.g. bpp-core.spec), and
    some ship a debian/ directory, so a lone file of the other kind is not
    proof. A .dsc file is: it describes a Debian source package and is not
    something upstream trees carry.
    """
    input_dir = worktree / "input"
    names = _top_level_names(input_dir)
    if names is None:
        return PackageFormat("unknown", "none", ["input/ is missing or unreadable"])

    dsc = [n for n in names if n.lower().endswith(".dsc")]
    specs = [n for n in names if n.lower().endswith(".spec")]
    try:
        control = (input_dir / "debian" / "control").is_file()
    except OSError:
        control = False

    evidence = []
    if dsc:
        evidence.append(f"{_quote(dsc)}: Debian source description")
    if control:
        evidence.append("input/debian/control: Debian packaging directory")
    if specs:
        evidence.append(f"{_quote(specs)}: RPM spec file")

    if dsc:
        if specs:
            evidence.append("the .spec file is treated as part of the upstream source "
                            "because a Debian source description is present")
        return PackageFormat("debian", "files", evidence)
    if control and specs:
        evidence.append("both Debian and RPM packaging files are present; format left unknown")
        return PackageFormat("unknown", "files", evidence)
    if control:
        return PackageFormat("debian", "files", evidence)
    if specs:
        return PackageFormat("rpm", "files", evidence)
    return PackageFormat("unknown", "none",
                         ["no .dsc, debian/control or .spec file at the top of input/"])


def detect_package_format(task: dict, manifest: dict, worktree: Path) -> PackageFormat:
    """Say whether the case is a Debian or an RPM package, and why.

    Case metadata (task.json, then the case manifest) is preferred. Only when it
    says nothing about the format are packaging files inspected. Disagreement is
    reported as "unknown" with every observation kept. Never raises; reads only.
    """
    votes = _metadata_votes("task.json", task) + _metadata_votes("manifest.json", manifest)
    evidence = [reason for _, reason in votes]
    found = {name for name, _ in votes if name}
    if len(found) == 1:
        return PackageFormat(found.pop(), "metadata", evidence)
    if len(found) > 1:
        return PackageFormat("unknown", "metadata",
                             evidence + ["case metadata disagrees about the format"])
    from_files = _from_files(worktree)
    from_files.evidence = evidence + from_files.evidence
    return from_files


# --- Loading a case -----------------------------------------------------------

def load_case(task_path: Path, *, manifest_path: Path | None = None,
              worktree: Path | None = None, log_path: Path | None = None) -> CaseInfo:
    """Load task.json and an optional local manifest without changing either.

    Task values take precedence. Missing optional fields remain None. Malformed
    JSON and missing explicitly requested files raise, rather than inventing a
    case identity. No path read from metadata is executed or written.

    Without an explicit manifest_path, the case's own manifest.json in the
    worktree is used when present (the platform places it there). That file is
    optional: if it cannot be read it is ignored and the reason is recorded.
    """
    task_path = Path(task_path)
    task = _object(task_path)

    def path_value(explicit, key, fallback):
        if explicit is not None:
            return Path(explicit)
        value = _string(task.get(key))
        path = Path(value) if value else fallback
        return path if path.is_absolute() else task_path.parent / path

    tree = path_value(worktree, "worktree", Path("../work/repo"))
    log = path_value(log_path, "initial_build_log", Path("initial-build.log"))

    manifest_note = None
    if manifest_path is not None:
        manifest = _object(Path(manifest_path))
    else:
        manifest = {}
        found = tree / "manifest.json"
        try:
            if found.is_file():
                manifest = _object(found)
        except (OSError, ValueError) as error:
            manifest_note = f"worktree manifest.json ignored ({type(error).__name__})"

    build = manifest.get("build")
    build = build if isinstance(build, dict) else {}
    task_build = task.get("build")
    task_build = task_build if isinstance(task_build, dict) else {}
    package = _string(task.get("package"), _nested(task, "package", "name"), task.get("package_name"),
                      manifest.get("package"), _nested(manifest, "package", "name"), manifest.get("package_name"))
    # Debian source recipe filenames encode the package before the version.
    recipe = _string(build.get("recipe"))
    if package is None and recipe and recipe.endswith(".dsc"):
        name = recipe.replace("\\", "/").rsplit("/", 1)[-1]
        if "_" in name:
            package = name.split("_", 1)[0]

    package_format = detect_package_format(task, manifest, tree)
    if manifest_note:
        package_format.evidence.insert(0, manifest_note)

    return CaseInfo(
        _string(task.get("case_id"), manifest.get("case_id")), package,
        _string(task.get("target_arch"), task.get("target_architecture"),
                _nested(task, "platform", "target_arch"), task_build.get("architecture"),
                manifest.get("target_arch"), _nested(manifest, "platform", "target_arch"), build.get("architecture")),
        tree, log, task, manifest, package_format,
    )
