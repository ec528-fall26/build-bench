"""Read an unchanged UTF-8 log tail with bounded backward reads."""

from pathlib import Path
import os


_BLOCK_SIZE = 64 * 1024


def extract_tail(path: Path, n_lines: int) -> str:
    """Return the last ``n_lines`` LF-delimited lines of a file.

    Preserve whitespace, blank lines, CRLF, and any final newline. Decode as
    UTF-8, replacing invalid byte sequences with U+FFFD. A final LF terminates
    the last line; it does not introduce an additional empty line.

    Zero lines returns an empty string without opening the file. Negative
    counts raise ValueError; non-integer counts (including bool) raise
    TypeError. Filesystem errors, including FileNotFoundError, propagate so
    the agent shell can report them instead of treating missing evidence as
    an empty log. The caller should provide a stable, seekable log file.

    Read backward in blocks, stopping once the requested tail is found.
    Time and memory depend on the requested tail, not the file's total size:
    O(tail bytes + block size). A very long requested line still requires
    enough memory to return it; this function never silently truncates it.
    """
    if isinstance(n_lines, bool) or not isinstance(n_lines, int):
        raise TypeError("n_lines must be an integer")
    if n_lines < 0:
        raise ValueError("n_lines must be non-negative")
    if n_lines == 0:
        return ""

    chunks: list[bytes] = []
    remaining = n_lines
    with path.open("rb") as log:
        position = log.seek(0, os.SEEK_END)
        first_block = True
        while position > 0:
            size = min(_BLOCK_SIZE, position)
            position -= size
            log.seek(position)
            block = log.read(size)
            if len(block) != size:
                raise OSError("log changed or could not be read completely")

            search_end = len(block)
            if first_block and block.endswith(b"\n"):
                search_end -= 1
            first_block = False

            delimiters = block.count(b"\n", 0, search_end)
            if delimiters >= remaining:
                for _ in range(remaining):
                    search_end = block.rfind(b"\n", 0, search_end)
                chunks.append(block[search_end + 1 :])
                break

            remaining -= delimiters
            chunks.append(block)

    # Decode once so UTF-8 characters split across read blocks stay intact.
    return b"".join(reversed(chunks)).decode("utf-8", errors="replace")
