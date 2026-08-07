from __future__ import annotations

import os
from pathlib import Path
import subprocess

_ENTRYPOINT = (
    Path(__file__).resolve().parents[1] / "scripts" / "docker" / "app-entrypoint.sh"
)


def test_entrypoint_requires_and_uses_admin_password(tmp_path: Path) -> None:
    log_path = tmp_path / "lens.log"
    (tmp_path / "mkdir").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "lens").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$LENS_TEST_LOG"\n'
    )
    (tmp_path / "mkdir").chmod(0o755)
    (tmp_path / "lens").chmod(0o755)

    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "LENS_SKIP_DB_UPGRADE": "1",
        "LENS_TEST_LOG": str(log_path),
    }
    missing = subprocess.run(
        ["sh", str(_ENTRYPOINT)],
        env=env,
        capture_output=True,
        check=False,
    )
    assert missing.returncode != 0
    assert b"LENS_ADMIN_PASSWORD" in missing.stderr

    configured = subprocess.run(
        ["sh", str(_ENTRYPOINT)],
        env=env | {"LENS_ADMIN_PASSWORD": "configured-password"},
        capture_output=True,
        check=False,
    )
    assert configured.returncode == 0
    assert log_path.read_text().splitlines() == [
        "seed-admin --username admin --password configured-password",
        "serve",
    ]
