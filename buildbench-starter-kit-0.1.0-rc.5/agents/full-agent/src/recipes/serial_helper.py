"""serial-helper-build: build a recursively-made helper once, before the parallel build.

html-xml-utils (resolute, arm64): Makefile.am has a suffix rule `.c.e:` whose
recipe runs `$(MAKE) cexport`. Under `make -j8` several of those start at once
and rebuild the same helper concurrently, which shows up as an lto1 internal
compiler error ("resolution sub id ... not in object file"), "Text file busy"
or "file too short". Building the helper first and then everything else passed
the validator with LTO, -O2 and hardening intact
(harness/replays/html-xml-utils-verified-serial-cexport.json, 276 s, 6 of 6
runs at -j8). Stripping LTO or optimisation is NOT part of this recipe.

Standard library only.
"""

from __future__ import annotations

import re

from .. import patterns
from ..types import Edit
from . import rules_mk
from .registry import Match, Recipe, Refusal, read, register

ID = "serial-helper-build"
_RULE = re.compile(r"^(?P<head>(?:\.[\w]+\.[\w]+|[^\s:=#]*%[^\s:=#]*|\$\([\w]+\))[^:=\n]*):(?!=)[^=\n]*$")
_RECURSIVE = re.compile(r"^\t[@+-]*\s*\$\(MAKE\)\s+(?:-[\w-]+\s+)*(?P<helper>[A-Za-z_][\w.-]*)\s*$")
_ICE = re.compile(r"lto1: internal compiler error|Text file busy|file too short|lto-wrapper: fatal error")
_SKIP_HELPERS = {"all", "install", "clean", "check", "distclean", "dist", "test", "tests", "default"}
MAX_MAKEFILES = 40


def recursive_helpers(worktree) -> list[tuple[str, str, str]]:
    """(file, rule head, helper) for each pattern rule that runs `$(MAKE) helper`."""
    found = []
    makefiles = [(rel, text) for rel, text in patterns._source_files(worktree, (".am", ".in"))
                 if rel.endswith(("Makefile.am", "Makefile.in"))][:MAX_MAKEFILES]
    for relative, text in makefiles:
        lines = text.split("\n")
        for index, line in enumerate(lines):
            rule = _RULE.match(line)
            if not rule:
                continue
            for following in lines[index + 1:index + 12]:
                if not following.startswith("\t"):
                    if following.strip() and not following.startswith("#"):
                        break
                    continue
                call = _RECURSIVE.match(following)
                if call and call.group("helper") not in _SKIP_HELPERS:
                    found.append((relative, rule.group("head").strip(), call.group("helper")))
    return found


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    helpers = recursive_helpers(worktree)
    if not helpers:
        return None
    text = [line for line in log_lines if isinstance(line, str)]
    indicator = next((i for i, line in enumerate(text) if _ICE.search(line)), None)
    for relative, head, helper in dict.fromkeys(helpers):
        name = re.escape(helper)
        duplicated = [i for i, line in enumerate(text)
                      if re.match(rf"^\s*(?:\S*/)?make(?:\[\d+\])?\s+(?:-\S+\s+)*{name}\s*$", line)]
        named = next((i for i, line in enumerate(text)
                      if re.search(rf"\*\*\* \[[^\]]*:\s*{name}\]|\./{name}\b|\b{name}:", line)
                      or (indicator is not None and re.search(rf"\b{name}\b", line) and _ICE.search(line))), None)
        if len(duplicated) >= 2 or (indicator is not None and named is not None):
            at = indicator if indicator is not None else duplicated[1]
            evidence = [f"{relative}: rule `{head}` runs `$(MAKE) {helper}`"]
            if len(duplicated) >= 2:
                evidence.append(f"log: `make {helper}` runs {len(duplicated)} times (line {duplicated[0] + 1}, {duplicated[1] + 1})")
            if indicator is not None:
                evidence.append(text[indicator])
            return Match(ID, evidence, params={"helper": helper, "file": relative, "rule": head}, line=at)
    return None


def expand(worktree, match: Match, params: dict) -> list:
    edits = _expand(worktree, match, params)
    for edit in edits:
        rules_mk.check_recipe_lines(edit.new_text)
    return edits


def _expand(worktree, match: Match, params: dict) -> list:
    helper = match.params["helper"]
    chosen = (params.get("_raw") or "").split()
    if chosen and re.fullmatch(r"[A-Za-z_][\w.-]*", chosen[0]) and chosen[0] != "auto":
        helper = chosen[0]
    path = "input/debian/rules"
    text = read(worktree, path)
    if not text:
        raise Refusal("input/debian/rules cannot be read.")
    if re.search(rf"dh_auto_build\s+--\s+{re.escape(helper)}\b", text):
        raise Refusal(f"debian/rules already builds {helper} before the rest.")
    lines = text.split("\n")
    # An existing override_dh_auto_build: merge into it, never replace its flags.
    for index, line in enumerate(lines):
        if re.match(r"^override_dh_auto_build(?:-arch|-indep)?\s*:", line):
            for at in range(index + 1, len(lines)):
                body = lines[at]
                if body.strip() and not body.startswith("\t"):
                    break
                call = re.match(r"^\t(?P<lead>[@+-]*)dh_auto_build(?P<rest>(?:\s+.*)?)$", body)
                if not call:
                    continue
                rest = call.group("rest").strip()
                if rest.startswith("--"):
                    new_call = f"\t{call.group('lead')}dh_auto_build -- {helper} {rest[2:].strip()}"
                elif rest:
                    new_call = f"\t{call.group('lead')}dh_auto_build {rest} -- {helper}"
                else:
                    new_call = f"\t{call.group('lead')}dh_auto_build -- {helper}"
                if text.count(body) != 1:
                    raise Refusal("the dh_auto_build line in the override is not unique.")
                return [Edit(path, body + "\n", new_call + "\n" + body + "\n")] if at + 1 < len(lines) else \
                       [Edit(path, body, new_call + "\n" + body)]
            raise Refusal("the existing override_dh_auto_build does not call dh_auto_build; "
                          f"add a line there that builds {helper} first.")
    block = f"override_dh_auto_build:\n\tdh_auto_build -- {helper}\n\tdh_auto_build\n\n"
    for index, line in enumerate(lines):
        if re.match(r"^override_\w+\s*:", line):
            anchor = line + "\n"
            if text.count(anchor) == 1:
                return [Edit(path, anchor, block + anchor)]
            raise Refusal("the first override target of debian/rules is not unique.")
    for index, line in enumerate(lines):
        if re.match(r"^\tdh\s+\$@", line) and index + 1 <= len(lines):
            # after the catch-all rule: append at its end
            end = index
            if text.count(line + "\n") == 1:
                return [Edit(path, line + "\n", line + "\n\n" + block.rstrip("\n") + "\n")]
    raise Refusal("debian/rules has no override target or `dh $@` rule to put the build step next to.")


def summary(edits, match, params) -> str:
    return (f"builds `{match.params['helper']}` first (`dh_auto_build -- {match.params['helper']}`), then runs "
            "the normal dh_auto_build; LTO, -O2 and hardening are untouched")


register(Recipe(
    ID, "verified",
    "Adds override_dh_auto_build to debian/rules that builds the recursively-made helper serially first "
    "(dh_auto_build -- <helper>), then dh_auto_build. new_text: empty, or the helper name.",
    recognise, expand, summary))
