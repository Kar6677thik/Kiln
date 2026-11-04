# tomllib.load() needs a file opened in BINARY mode ("rb").
import tempfile
import tomllib
from pathlib import Path

with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "kiln.toml"
    path.write_text("[master]\nworkers = 4\n", encoding="utf-8")
    with path.open("rb") as file:
        print("binary mode:", tomllib.load(file))
    with path.open("r", encoding="utf-8") as file:
        try:
            tomllib.load(file)
        except TypeError as error:
            print("text mode:", type(error).__name__, "-", error)
