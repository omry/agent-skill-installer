from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tarfile
import zipfile

from tools import reproducible_artifacts


def write_sdist(path: Path, *, mtime: int, uid: int) -> None:
    with tarfile.open(path, "w:gz") as archive:
        directory = tarfile.TarInfo("example-1.0/")
        directory.type = tarfile.DIRTYPE
        directory.mtime = mtime
        directory.uid = uid
        archive.addfile(directory)

        payload = b"same contents\n"
        member = tarfile.TarInfo("example-1.0/README.md")
        member.size = len(payload)
        member.mtime = mtime
        member.uid = uid
        archive.addfile(member, BytesIO(payload))


def write_wheel(path: Path, *, timestamp: tuple[int, int, int, int, int, int]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        member = zipfile.ZipInfo("example/__init__.py", timestamp)
        member.external_attr = 0o644 << 16
        archive.writestr(member, b"__version__ = '1.0'\n")


def test_normalization_makes_equivalent_archives_byte_identical(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_sdist = first / "example-1.0.tar.gz"
    second_sdist = second / "example-1.0.tar.gz"
    first_wheel = first / "example-1.0-py3-none-any.whl"
    second_wheel = second / "example-1.0-py3-none-any.whl"

    write_sdist(first_sdist, mtime=1_700_000_000, uid=1000)
    write_sdist(second_sdist, mtime=1_710_000_000, uid=2000)
    write_wheel(first_wheel, timestamp=(2024, 1, 1, 0, 0, 0))
    write_wheel(second_wheel, timestamp=(2025, 2, 2, 2, 2, 2))

    epoch = 1_720_000_000
    reproducible_artifacts.normalize_distributions(first, epoch)
    reproducible_artifacts.normalize_distributions(second, epoch)

    expected_sdist = first_sdist.read_bytes()
    expected_wheel = first_wheel.read_bytes()
    assert expected_sdist == second_sdist.read_bytes()
    assert expected_wheel == second_wheel.read_bytes()

    reproducible_artifacts.normalize_distributions(first, epoch)
    assert first_sdist.read_bytes() == expected_sdist
    assert first_wheel.read_bytes() == expected_wheel

    with tarfile.open(first_sdist, "r:gz") as archive:
        readme = archive.extractfile("example-1.0/README.md")
        assert readme is not None
        assert readme.read() == b"same contents\n"
    with zipfile.ZipFile(first_wheel) as archive:
        assert archive.read("example/__init__.py") == b"__version__ = '1.0'\n"
