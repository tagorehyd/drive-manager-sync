import os
import sys
import tempfile
from pathlib import Path

_BASE = Path(tempfile.mkdtemp(prefix="dm-test-"))
(_BASE / "mnt").mkdir()
(_BASE / "fstab").write_text("UUID=abc / ext4 defaults 0 1\n", encoding="utf-8")

os.environ.update({
    "CONFIG_DIR": str(_BASE / "config"),
    "DATA_DIR": str(_BASE / "data"),
    "BROWSE_ROOTS": str(_BASE),
    "MNT_ROOT": str(_BASE / "mnt"),
    "FSTAB_PATH": str(_BASE / "fstab"),
    "DM_HOST_MODE": "direct",
    "DM_SKIP_STARTUP_TASKS": "1",
    "MOUNT_HEAL_DELAY": "0",
    "SYNC_RETRY_DELAY": "0",
    "MEDIA_MIN_FILE_AGE": "0",
})
os.environ.pop("OPENAI_COMPATIBLE_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

config.bootstrap()
