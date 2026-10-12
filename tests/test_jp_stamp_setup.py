"""Check provisioning fails closed and keeps signing material private."""

import base64
import importlib.util
import json
from pathlib import Path
import stat

import pytest

spec = importlib.util.spec_from_file_location(
    "jp_stamp_setup", Path(__file__).resolve().parents[1] / "scripts/jp_stamp_setup.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_provisioning_rejects_inconsistent_native_samples():
    timestamps = [1000000000, 1700000000, 9999999999]
    samples = [
        {"timestamp": t, "stamp": base64.b64encode(f"{setup.SUPPORTED_APP_ID}:{t}".encode()).decode()}
        for t in timestamps
    ]
    result = {"app_id": setup.SUPPORTED_APP_ID, "samples": samples}
    config = setup.derive_config(result, timestamps)
    assert base64.b64decode(config["cfb"]) == bytes(47)
    changed = bytearray(base64.b64decode(samples[-1]["stamp"]))
    changed[-1] ^= 1
    samples[-1]["stamp"] = base64.b64encode(changed).decode()
    with pytest.raises(ValueError, match="varies"):
        setup.derive_config(result, timestamps)


def test_provisioning_writes_private_file_and_never_overwrites(tmp_path):
    path = tmp_path / "signing.json"
    config = {"cfb": "synthetic signing data"}
    setup.write_config(path, config)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        setup.write_config(path, {"cfb": "replacement"})
    assert json.loads(path.read_text()) == config
