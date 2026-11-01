#!/usr/bin/env python3
"""Authorized GET /users/me only. Never accepts a token in argv or prints the raw response."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))
from aicommerce.providers.ml_readonly import MLReadOnly


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--token-file', type=Path, required=True)
    p.add_argument('--authorized', action='store_true', help='Owner has already consented to OAuth and this GET')
    a = p.parse_args()
    if not a.authorized:
        p.error('Explicit prior OAuth consent and read-only authorization required')
    fd = os.open(a.token_file, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid() or info.st_size > 16384:
            p.error('Token file must be a private regular file owned by this user (0600)')
        token = stream.read().strip()
    result = MLReadOnly(token).receipt()
    del token
    print(json.dumps(result, indent=2))
    return 0 if result['state'] == 'READ_ONLY_VERIFICADO' else 1


if __name__ == '__main__':
    raise SystemExit(main())
