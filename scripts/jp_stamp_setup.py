# /// script
# requires-python = ">=3.10"
# dependencies = ["frida-tools>=14,<15"]
# ///
"""Provision local JP signing data once from the user's running Android app.

Regular Home Assistant requests then need neither Android nor Frida. Signing
data is written to a new private file and is never printed to the terminal.
"""

import argparse
import base64
from contextlib import suppress
import json
import os
from pathlib import Path
import time

# This is the application identifier verified by the integration, not a key.
SUPPORTED_APP_ID = "8be122e0-b0d9-40b5-a46e-67b25194ba88"

_AGENT = """
const Java = bridge;
rpc.exports.samples = function(timestamps) {
  return new Promise(function(resolve, reject) {
    Java.perform(function() {
      let config = null;
      Java.choose('hmns.global.mobile.application.MyApiConfig', {
        onMatch: function(instance) {
          if (instance.e.value) { config = Java.retain(instance); return 'stop'; }
        },
        onComplete: function() {
          try {
            if (config === null) throw new Error('App configuration unavailable');
            const appId = config.e.value;
            const C = Java.use('hmns.global.mobile.application.MyApiConfig');
            const B = Java.use('android.util.Base64');
            const samples = timestamps.map(function(t) {
              return {
                timestamp: t,
                stamp: B.encodeToString(C.stringFromJNI(appId + ':' + t), 2)
              };
            });
            resolve({app_id: appId, samples: samples});
          } catch(e) { reject(new Error('Native signing comparison failed')); }
        }
      });
    });
  });
};
"""


def derive_config(result: dict, timestamps: list[int]) -> dict[str, str]:
    """Accept signing data only when all independent native samples agree."""
    if result.get("app_id") != SUPPORTED_APP_ID:
        raise ValueError("Unsupported JP application ID")
    samples = result.get("samples", [])
    if len(samples) != len(timestamps) or len(set(timestamps)) < 3:
        raise ValueError("Not enough independent signing samples")
    masks = []
    for sample, timestamp in zip(samples, timestamps):
        if sample.get("timestamp") != timestamp:
            raise ValueError("Native sample timestamp mismatch")
        raw = f"{SUPPORTED_APP_ID}:{timestamp}".encode()
        signed = base64.b64decode(sample["stamp"], validate=True)
        if len(raw) != 47 or len(signed) != len(raw):
            raise ValueError("Unsupported native Stamp format")
        masks.append(bytes(a ^ b for a, b in zip(raw, signed)))
    if any(mask != masks[0] for mask in masks[1:]):
        raise ValueError("Native signing data varies with the timestamp")
    return {
        "app_id": SUPPORTED_APP_ID,
        "cfb": base64.b64encode(masks[0]).decode(),
    }


def write_config(path: Path, config: dict[str, str]) -> None:
    """Create a private file without replacing any existing file."""
    content = json.dumps(config, indent=2) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)


def main() -> None:
    import frida
    import frida_tools

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int, help="Official app PID")
    parser.add_argument("--frida-address", default="127.0.0.1:27042")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new private file")
    session = None
    script = None
    try:
        device = frida.get_device_manager().add_remote_device(args.frida_address)
        session = device.attach(args.pid)
        bridge = Path(frida_tools.__file__).parent / "bridges" / "java.js"
        script = session.create_script(bridge.read_text(encoding="utf-8") + "\n" + _AGENT)
        script.load()
        timestamps = [1000000000, 1700000000, 1700000001, 1700011111,
                      int(time.time()), 1800000000, 2000000000, 9999999999]
        config = derive_config(script.exports_sync.samples(timestamps), timestamps)
        write_config(args.output, config)
    except Exception:  # noqa: BLE001
        # Frida messages may include runtime data; never emit the raw error.
        parser.exit(1, "Setup failed; signing data was not printed. Check the app, Frida and output path.\n")
    finally:
        if script is not None:
            with suppress(frida.InvalidOperationError, frida.TransportError):
                script.unload()
        if session is not None:
            with suppress(frida.InvalidOperationError, frida.TransportError):
                session.detach()
    print(f"Verified {len(timestamps)} native samples. Configuration saved to {args.output}.")  # noqa: T201
    print("Enter the file's cfb value in the integration Options. Android can then be stopped.")  # noqa: T201


if __name__ == "__main__":
    main()
