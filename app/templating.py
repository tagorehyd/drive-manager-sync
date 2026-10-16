from __future__ import annotations

from fastapi.templating import Jinja2Templates

from app.config import APP_DIR

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
