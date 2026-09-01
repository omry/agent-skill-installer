#!/usr/bin/env python3
"""Normalize Python distribution archives for reproducible byte output."""

from __future__ import annotations

import argparse
from copy import copy
from datetime import datetime, timezone
import gzip
from io import BytesIO
import os
from pathlib import Path
import tarfile
import tempfile
import zipfile


def _replace_atomically(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(data)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def normalize_sdist(path: Path, epoch: int) -> None:
    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(path, "r:gz") as source:
        for original in source.getmembers():
            member = copy(original)
            payload: bytes | None = None
            if member.isfile():
                extracted = source.extractfile(original)
                if extracted is None:
                    raise ValueError(f"could not read {original.name} from {path}")
                payload = extracted.read()
            member.mtime = epoch
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.pax_headers = {}
            entries.append((member, payload))

    output = BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        fileobj=output,
        compresslevel=9,
        mtime=epoch,
    ) as compressed:
        with tarfile.open(
            fileobj=compressed,
            mode="w",
            format=tarfile.PAX_FORMAT,
        ) as destination:
            for member, payload in sorted(entries, key=lambda entry: entry[0].name):
                destination.addfile(
                    member,
                    BytesIO(payload) if payload is not None else None,
                )
    _replace_atomically(path, output.getvalue())


def _zip_timestamp(epoch: int) -> tuple[int, int, int, int, int, int]:
    value = datetime.fromtimestamp(epoch, timezone.utc)
    if value.year < 1980:
        value = datetime(1980, 1, 1, tzinfo=timezone.utc)
    return (
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second - value.second % 2,
    )


def normalize_wheel(path: Path, epoch: int) -> None:
    timestamp = _zip_timestamp(epoch)
    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    with zipfile.ZipFile(path, "r") as source:
        archive_comment = source.comment
        for original in source.infolist():
            member = zipfile.ZipInfo(original.filename, timestamp)
            member.compress_type = original.compress_type
            member.comment = original.comment
            member.create_system = original.create_system
            member.external_attr = original.external_attr
            member.internal_attr = original.internal_attr
            entries.append((member, source.read(original.filename)))

    output = BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=True) as destination:
        destination.comment = archive_comment
        for member, payload in sorted(entries, key=lambda entry: entry[0].filename):
            destination.writestr(member, payload, compresslevel=9)
    _replace_atomically(path, output.getvalue())


def normalize_distributions(directory: Path, epoch: int) -> list[Path]:
    artifacts = sorted(path for path in directory.iterdir() if path.is_file())
    if not artifacts:
        raise ValueError(f"no distribution artifacts found in {directory}")
    for artifact in artifacts:
        if artifact.name.endswith(".tar.gz"):
            normalize_sdist(artifact, epoch)
        elif artifact.suffix == ".whl":
            normalize_wheel(artifact, epoch)
        else:
            raise ValueError(f"unsupported distribution artifact: {artifact.name}")
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("epoch", type=int)
    arguments = parser.parse_args()
    normalize_distributions(arguments.directory, arguments.epoch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
