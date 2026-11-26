import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]


def available_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def wait_ready(client, url):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            if client.get(url).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise RuntimeError("Local demo process did not become ready; inspect its log")


def main():
    processes = []
    with tempfile.TemporaryDirectory(prefix="bi-smoke-") as scratch:
        scratch = Path(scratch)
        erp_port, api_port = available_port(), available_port()
        while api_port == erp_port:
            api_port = available_port()
        config = yaml.safe_load((ROOT / "config/sources.demo.yaml").read_text(encoding="utf-8"))
        for source in config["sources"]:
            source["base_url"] = source["base_url"].replace(":8010", f":{erp_port}")
        config_file = scratch / "sources.yaml"
        config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
        env = {
            **os.environ,
            "BI_DATABASE_URL": f"sqlite:///{(scratch / 'demo.db').as_posix()}",
            "BI_SOURCES_FILE": str(config_file),
            "BI_ALLOWED_SOURCE_HOSTS": '["127.0.0.1"]',
            "BI_API_KEY": "local-smoke-test",
            "DEMO_1C_USER": "demo",
            "DEMO_1C_PASSWORD": "demo",
            "PYTHONUTF8": "1",
        }
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
        )
        log_file = scratch / "server.log"
        with log_file.open("w+", encoding="utf-8") as log:
            try:
                for app, port in [
                    ("bi_backend.demo_sources:app", erp_port),
                    ("bi_backend.main:app_factory", api_port),
                ]:
                    command = [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        app,
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--no-access-log",
                    ]
                    if "app_factory" in app:
                        command.append("--factory")
                    processes.append(
                        subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log)
                    )
                with httpx.Client(timeout=30, trust_env=False) as client:
                    base = f"http://127.0.0.1:{api_port}"
                    wait_ready(client, f"http://127.0.0.1:{erp_port}/health")
                    wait_ready(client, base + "/health")
                    headers = {"X-API-Key": "local-smoke-test"}
                    imported = {}
                    for source_id in ["onec_demo", "bitrix_demo", "onec_demo"]:
                        response = client.post(
                            f"{base}/api/v1/sources/{source_id}/sync", headers=headers
                        )
                        response.raise_for_status()
                        imported[source_id] = response.json()["records_count"]
                    sales = client.get(base + "/api/v1/sales", headers=headers).json()
                    recommendations = client.get(
                        base + "/api/v1/recommendations", headers=headers
                    ).json()
                    assert imported == {"onec_demo": 60, "bitrix_demo": 125}
                    assert sales["total"] == 185
                    assert recommendations["items"]
                    print(
                        json.dumps(
                            {
                                "imported": imported,
                                "total": sales["total"],
                                "repeated_sync": "no_duplicates",
                                "recommendations": len(recommendations["items"]),
                            }
                        )
                    )
            except Exception:
                log.flush()
                print(log_file.read_text(encoding="utf-8"), file=sys.stderr)
                raise
            finally:
                for process in processes:
                    if process.poll() is not None:
                        continue
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                    else:
                        process.terminate()
                for process in processes:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    main()
