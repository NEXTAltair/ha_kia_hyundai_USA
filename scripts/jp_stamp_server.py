# /// script
# requires-python = ">=3.10"
# dependencies = ["frida-tools>=14,<15"]
# ///
"""Serve fresh JP Stamps from the user's running official Android app.

Run with `uv run scripts/jp_stamp_server.py --pid <app PID>` after forwarding
the emulator's Frida port to localhost. No app binary or signing key is
distributed here; the installed app generates the signature. No account
credentials are read. The server binds to localhost unless --host is set.
"""

import argparse
from contextlib import suppress
import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import frida
import frida_tools

_LOGGER = logging.getLogger(__name__)

_AGENT = """
const Java = bridge;
let config = null;
rpc.exports.generate = function () {
  return new Promise(function (resolve, reject) {
    Java.perform(function () {
      function generate(instance) {
        try {
          const appId = instance.e.value;
          if (!appId) throw new Error('Application ID unavailable');
          const C = Java.use('hmns.global.mobile.application.MyApiConfig');
          const bytes = C.stringFromJNI(appId + ':' + Math.floor(Date.now()/1000));
          const stamp = Java.use('android.util.Base64').encodeToString(bytes, 2);
          resolve({app_id: appId, stamp: stamp});
        } catch (e) { reject(new Error('Official app signing failed')); }
      }
      if (config !== null) { generate(config); return; }
      Java.choose('hmns.global.mobile.application.MyApiConfig', {
        onMatch: function (instance) {
          if (instance.e.value) {
            config = Java.retain(instance);
            return 'stop';
          }
        },
        onComplete: function () {
          if (config === null) reject(new Error('Open the official Bluelink app first'));
          else generate(config);
        }
      });
    });
  });
};
"""


class Signer:
    """Keep the signing session alive and reconnect after an agent unload."""

    def __init__(self, address: str, pid: int):
        self.address = address
        self.pid = pid
        self.session = None
        self.script = None

    def connect(self):
        device = frida.get_device_manager().add_remote_device(self.address)
        self.session = device.attach(self.pid)
        bridge = Path(frida_tools.__file__).parent / "bridges" / "java.js"
        self.script = self.session.create_script(
            bridge.read_text(encoding="utf-8") + "\n" + _AGENT
        )
        self.script.load()

    def close(self):
        if self.script is not None:
            with suppress(frida.InvalidOperationError, frida.TransportError):
                self.script.unload()
            self.script = None
        if self.session is not None:
            with suppress(frida.InvalidOperationError, frida.TransportError):
                self.session.detach()
            self.session = None

    def generate(self):
        for attempt in range(2):
            try:
                if self.script is None:
                    self.connect()
                return self.script.exports_sync.generate()
            except (frida.InvalidOperationError, frida.TransportError):
                self.close()
                if attempt:
                    raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pid", required=True, type=int, help="Official app PID from adb shell pidof"
    )
    parser.add_argument("--frida-address", default="127.0.0.1:27042")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    signer = Signer(args.frida_address, args.pid)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/stamp":
                self.send_error(404)
                return
            try:
                result = signer.generate()
                body = json.dumps(result).encode("utf-8")
            except Exception:  # noqa: BLE001
                # Do not include Frida messages or application data in errors.
                self.send_error(503, "Official app signer unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            # Request logging could expose URL/query data; suppress it.
            pass

    try:
        with HTTPServer((args.host, args.port), Handler) as server:
            _LOGGER.info("JP Stamp service listening on %s:%s", args.host, args.port)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signer.close()


if __name__ == "__main__":
    main()
