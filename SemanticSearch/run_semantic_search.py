"""Direct launcher compatible with Aicy's existing `python -I script serve-mcp` connection."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from semantic_search.cli import main


if __name__ == "__main__":
    main()
