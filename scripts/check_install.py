"""Build a wheel and verify the installed API/static assets away from the checkout.

Uses a disposable venv with existing runtime dependencies; it does not prove that
public package indexes are available. Run from a configured development environment.
"""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="open-liq-install-") as directory:
        temp = Path(directory)
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", str(ROOT), "-w", str(temp)], check=True)
        wheel = next(temp.glob("open_liquidation_heatmap-*.whl"))
        venv.create(temp / "venv", with_pip=True, system_site_packages=True)
        python = temp / "venv/bin/python"
        subprocess.run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], check=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        with (temp / "server.log").open("w+") as log:
            process = subprocess.Popen([str(temp / "venv/bin/liqmap"), "--port", str(port)], cwd=temp, env=env, stdout=log, stderr=log)
            try:
                base = f"http://127.0.0.1:{port}"
                for _ in range(100):
                    if process.poll() is not None:
                        log.seek(0)
                        raise AssertionError(log.read())
                    try:
                        with urllib.request.urlopen(base + "/healthz", timeout=1) as response:
                            assert response.status == 200
                        break
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(0.1)
                else:
                    raise AssertionError("installed service did not start")
                for path, expected in [("/", b"<html"), ("/app.js", b"const"), ("/assets/logo.png", b"\x89PNG")]:
                    with urllib.request.urlopen(base + path, timeout=5) as response:
                        assert expected in response.read(), path
                with urllib.request.urlopen(base + "/api/v1/heatmap?exchange=demo&symbol=BTC&range=12h&model=1", timeout=30) as response:
                    body = json.load(response)
                    assert body["source"] == "demo" and body["schema_version"] == "1.0", body.keys()
                print("PASS: installed console entry point, packaged assets and demo API outside checkout")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
