import secrets
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[1]
env_file = root / ".env"
if env_file.exists():
    raise SystemExit(".env already exists; kept unchanged")
template = (root / ".env.example").read_text(encoding="utf-8")
for key in ["POSTGRES_PASSWORD", "WAREHOUSE_PASSWORD", "METABASE_PASSWORD", "BI_READER_PASSWORD"]:
    lines = template.splitlines()
    template = (
        "\n".join(
            f"{key}={secrets.token_hex(24)}" if line.startswith(f"{key}=") else line
            for line in lines
        )
        + "\n"
    )
template = template.replace("BI_API_KEY=\n", f"BI_API_KEY={secrets.token_hex(32)}\n")
env_file.write_text(template, encoding="utf-8")
config = yaml.safe_load((root / "config/sources.demo.yaml").read_text(encoding="utf-8"))
for source in config["sources"]:
    source["base_url"] = source["base_url"].replace("127.0.0.1:8010", "demo-sources:8010")
(root / "config/sources.compose.yaml").write_text(
    yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
)
print("Created .env and config/sources.compose.yaml; credentials were not printed.")
