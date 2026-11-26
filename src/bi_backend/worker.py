import json
import time

from bi_backend.config import Settings
from bi_backend.db import make_database
from bi_backend.errors import PipelineError
from bi_backend.pipeline import SyncService
from bi_backend.sources import load_sources


def main():
    settings = Settings()
    sources = load_sources(settings.sources_file)
    engine, sessions = make_database(settings.database_url)
    service = SyncService(sessions, settings)
    service.register(sources)
    due = dict.fromkeys(sources, 0.0)
    try:
        while True:
            for source in sources.values():
                if time.monotonic() < due[source.id]:
                    continue
                try:
                    run = service.sync(source)
                    print(
                        json.dumps({"source": source.id, "run": run.id, "status": run.status}),
                        flush=True,
                    )
                except PipelineError as exc:
                    print(json.dumps({"source": source.id, "code": exc.code}), flush=True)
                due[source.id] = time.monotonic() + source.poll_seconds
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
