"""Atomic, encoded JSON file persistence shared by the stores.

Each store keeps its own lock and domain logic; these helpers own the JSON
(de)serialization, the ``crypto`` encode/decode at rest, and the atomic
temp-file -> ``os.replace`` write. ``load_encoded`` also reports the on-disk
algorithm so a caller can migrate to a stronger one (see ``MailStore.load``).
"""

import json
import logging
from pathlib import Path

from . import crypto, util

log = logging.getLogger(__name__)


def load_encoded(cache_file):
    """Return ``(obj, alg)`` decoded from an encoded JSON file.

    ``(None, None)`` if the file is absent or cannot be decoded / parsed.
    """
    path = Path(cache_file)
    if not path.exists():
        return None, None
    try:
        payload, alg = crypto.decode(path.read_bytes())
        return json.loads(payload), alg
    except Exception:
        log.exception("Failed to load %s", path.name)
        return None, None


def save_encoded(cache_file, obj):
    """Serialize ``obj`` to JSON, encode it, and atomically write to ``cache_file``."""
    path = Path(cache_file)
    payload = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    temp_file = path.with_name(path.name + ".tmp")
    with open(temp_file, "wb") as f:
        f.write(crypto.encode(payload))
    util.atomic_replace(temp_file, path)
