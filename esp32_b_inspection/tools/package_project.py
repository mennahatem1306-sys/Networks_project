from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def package(destination):
    root = Path(__file__).resolve().parents[1]
    excluded = {"build", "managed_components", "__pycache__", ".git", ".ruff_cache"}
    excluded_names = {"sdkconfig", "sdkconfig.old", "compile.log"}
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for source in sorted(root.rglob("*")):
            relative = source.relative_to(root)
            if (
                not source.is_file()
                or excluded.intersection(relative.parts)
                or source.name in excluded_names
                or source.suffix in {".pyc", ".jsonl", ".zip"}
            ):
                continue
            archive.write(source, Path("aya_inspection_idf") / relative)
    return Path(destination).resolve()


if __name__ == "__main__":
    print(
        package(
            Path(__file__).resolve().parents[2] / "Aya_ESP32B_ESP-IDF_Milestone1.zip"
        )
    )
