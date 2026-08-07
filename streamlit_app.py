"""Entry point for Streamlit Community Cloud.

Streamlit Cloud runs from the repository root and does not know about the `src/` layout, so
this puts `src` on the path and imports the screen. Importing it runs it — see
`src/cms_outliers/app/main.py`, which is the real code and what you run locally.

The hosted deploy has no fact tables (941 MB, a build artifact), so the screen serves peer
distributions from the 3.3 MB `peer_stats` in this repo and says plainly what it cannot show.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from cms_outliers.app import main  # noqa: E402, F401
