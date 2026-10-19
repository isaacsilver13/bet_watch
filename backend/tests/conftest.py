from pathlib import Path

import app.config

# Tests must not depend on a developer's local .env (which may hold a real
# THE_ODDS_API_KEY and flip the app into live mode at import time).
app.config._DOTENV_PATH = Path(__file__).parent / "does-not-exist.env"


