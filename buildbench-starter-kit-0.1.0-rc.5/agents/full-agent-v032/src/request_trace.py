"""Optional local capture of messages passed to a completion callback."""

import json
import os
from pathlib import Path


def trace_completion(complete):
    """Wrap the callback only when BB_MODEL_TRACE is configured.

    Capture message contents, not HTTP headers, client settings or environment.
    Refuse an existing destination on the first call to avoid mixing runs.
    A recorded request proves callback invocation, not successful API delivery.
    """
    destination = os.environ.get("BB_MODEL_TRACE")
    if not destination:
        return complete

    path = Path(destination).expanduser()
    calls = 0

    def traced(messages):
        nonlocal calls
        record = {
            "call": calls + 1,
            "messages": messages,
        }
        serialized = json.dumps(record, ensure_ascii=True) + "\n"
        flags = os.O_WRONLY | os.O_NOFOLLOW
        if calls == 0:
            flags |= os.O_CREAT | os.O_EXCL
        else:
            flags |= os.O_APPEND

        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(serialized)
        calls += 1
        return complete(messages)

    return traced
