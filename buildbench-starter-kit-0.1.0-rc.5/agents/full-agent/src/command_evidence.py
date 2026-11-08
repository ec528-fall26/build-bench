"""Small, cited command reference for review; no execution or network access.

Facts describe documented tools, not the installed environment. Extend with
reviewed primary documentation, never with hidden validator results.
"""
import re

_SPHINX = re.compile(r'(?<![\w.-])(?:dh_sphinxdoc|sphinx-build|build_sphinx)(?![\w.-])')

_SPHINX_REFERENCE = """=== Command reference: Sphinx documentation ===
Reference knowledge, not target-environment observations or build feedback.
Debian sphinx 7.2.6-2 documents dh_sphinxdoc as a packaging helper for already
built documentation installed in the package staging tree. It does not generate
the documentation. Calling it or enabling --with sphinxdoc is not evidence
that a required HTML output directory will be created.
Source: https://sources.debian.org/src/sphinx/7.2.6-2/debian/dh-sphinxdoc/dh_sphinxdoc
Sphinx documents sphinx-build as the generator. With -b html, it writes HTML
to the supplied output directory; with -M html, HTML goes in the html subdirectory
of the supplied build directory. It reads source configuration, normally conf.py.
Source: https://www.sphinx-doc.org/en/master/man/sphinx-build.html
Determine the actual source directory, output directory, working directory and
options from this package's source/configuration. Existing Makefile targets may
already express them. Do not invent paths or assume tool availability/version.
These references do not establish that an arbitrary command will succeed."""


def command_evidence(edits):
    """Supply relevant documented semantics once, only for recipe changes."""
    for edit in edits:
        if edit.path.removeprefix('input/') != 'debian/rules':
            continue
        if _SPHINX.search(edit.old_text + '\n' + edit.new_text):
            return _SPHINX_REFERENCE
    return ''
