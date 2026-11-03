# Run with: python docs/python/snippets/modules-and-imports_1.py
# Shows importing a package and a module inside it, and where Python looked.
import sys
from pathlib import Path

import shapes
from shapes.circle import area

print("area(2) =", round(area(2), 2))
print("shapes came from:", Path(shapes.__file__).parent.name + "/" + Path(shapes.__file__).name)
print("first place Python searched:", Path(sys.path[0]).name)
import shapes  # noqa: E402  (importing again does NOT run __init__.py a second time)

print("'shapes' in sys.modules:", "shapes" in sys.modules)
