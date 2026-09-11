"""Map validated absolute image paths beneath a supplied filesystem root."""

from pathlib import Path, PurePosixPath


def image_path(root: Path, absolute: str) -> Path:
    path = PurePosixPath(absolute)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"image path is not absolute and contained: {absolute}")
    return root.joinpath(*path.parts[1:])


__all__ = ["image_path"]
