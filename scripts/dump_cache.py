"""Decrypt the mail cache into a human-readable JSON copy for debugging.

The mail cache (``mailfilter/mail_cache.json``) is encoded at rest behind the
versioned ``MFC1`` header — DPAPI-encrypted on Windows, base64-obfuscated
elsewhere (see ``mailfilter/crypto.py`` and ``docs/system-design.md`` §6). That
makes it impossible to eyeball what the app actually has cached. This script
reads the cache, decodes it with the same ``crypto`` module the app uses, and
writes a plain pretty-printed JSON copy you can open and grep.

Because DPAPI keys are user- and machine-scoped, a DPAPI-encoded cache only
decrypts on the same Windows account that wrote it. A base64 cache decodes
anywhere.

Usage::

    python scripts/dump_cache.py                       # -> mail_cache.decrypted.json
    python scripts/dump_cache.py -o /tmp/cache.json     # choose the output path
    python scripts/dump_cache.py --grep "invoice"       # also list matching mails
    python scripts/dump_cache.py --dry-run              # report, write nothing

The script is read-only against the real cache: it never writes the encoded
cache file, and it refuses to write its decrypted output on top of the cache or
over an existing file (use ``--force`` to overwrite a previous dump).
"""

import argparse
import json
import sys
from pathlib import Path

# Allow ``python scripts/dump_cache.py`` from anywhere: put the project root
# (this file's parent's parent) on the path so ``config`` and ``mailfilter``
# import cleanly without an installed package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from mailfilter import crypto  # noqa: E402

DEFAULT_OUTPUT = config.CACHE_FILE.with_name("mail_cache.decrypted.json")


def load_decoded(cache_file):
    """Return ``(mails, alg)`` decoded from the on-disk cache.

    Raises ``FileNotFoundError`` if the cache is absent, and surfaces any
    decode/parse error (e.g. DPAPI on the wrong account) to the caller.
    """
    path = Path(cache_file)
    if not path.exists():
        raise FileNotFoundError(f"cache file not found: {path}")
    payload, alg = crypto.decode(path.read_bytes())
    return json.loads(payload), alg


def grep_mails(mails, needle):
    """Indices + one-line summaries of mails whose subject/sender/body matches.

    Case-insensitive substring over the fields most useful for "is this specific
    message even in the cache?": subject, sender name/email, and body.
    """
    needle = needle.lower()
    hits = []
    for i, mail in enumerate(mails):
        if not isinstance(mail, dict):
            continue
        haystack = " ".join(
            str(mail.get(k, ""))
            for k in ("subject", "sender", "sender_email", "body")
        ).lower()
        if needle in haystack:
            hits.append(
                (
                    i,
                    f"[{mail.get('received', '?')}] "
                    f"{mail.get('sender', '?')} <{mail.get('sender_email', '')}> "
                    f"- {mail.get('subject', '(no subject)')}"
                )
            )
    return hits


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Decrypt the mail cache into a human-readable JSON copy."
    )
    parser.add_argument(
        "-c",
        "--cache",
        default=str(config.CACHE_FILE),
        help=f"path to the encoded cache (default: {config.CACHE_FILE})",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"where to write the decrypted JSON (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--grep",
        metavar="TEXT",
        help="print indices/summaries of mails matching TEXT (subject/sender/body)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite the output file if it already exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written without writing anything",
    )
    args = parser.parse_args(argv)

    cache_path = Path(args.cache)
    output_path = Path(args.output)

    # Guard: never let the decrypted (plaintext) dump land on the encoded cache.
    if output_path.resolve() == cache_path.resolve():
        parser.error("refusing to overwrite the encoded cache with a decrypted dump")

    print(f"Reading cache: {cache_path}")
    try:
        mails, alg = load_decoded(cache_path)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    except crypto.CacheCipherUnavailable as e:
        print(
            f"Error: cannot decrypt this cache here ({e}). A DPAPI-encoded "
            "cache only decrypts on the Windows account that wrote it."
        )
        return 1
    except Exception as e:  # noqa: BLE001 - report any decode/parse failure plainly
        print(f"Error: failed to decode/parse the cache: {e}")
        return 1

    count = len(mails) if isinstance(mails, list) else "?"
    print(f"Decoded {count} mail(s) (on-disk encoding: {crypto.alg_name(alg)})")

    if args.grep:
        hits = grep_mails(mails if isinstance(mails, list) else [], args.grep)
        print(f'\nMatches for "{args.grep}": {len(hits)}')
        for index, summary in hits:
            print(f"  #{index}  {summary}")
        print()

    if args.dry_run:
        print(f"[dry-run] would write decrypted JSON to: {output_path}")
        return 0

    if output_path.exists() and not args.force:
        print(
            f"Error: {output_path} already exists. Use --force to overwrite, or "
            "choose another path with -o."
        )
        return 1

    output_path.write_text(
        json.dumps(mails, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote decrypted JSON to: {output_path}")
    print(
        "Note: this is the on-disk form, so derived '_' fields are absent "
        "(they are stripped before the cache is written)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
