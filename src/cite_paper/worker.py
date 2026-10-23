from __future__ import annotations

import logging
import threading

from cite_paper.config import Settings
from cite_paper.store import LeaseLost, Store
from cite_paper.workflow import ResearchWorkflow

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings

    def once(self) -> bool:
        run = self.store.claim(self.settings.lease_seconds, self.settings.max_attempts)
        if run is None:
            return False
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(max(0.05, self.settings.lease_seconds / 3)):
                try:
                    self.store.heartbeat(run["id"], run["lease_token"], self.settings.lease_seconds)
                except (LeaseLost, InterruptedError):
                    return
                except Exception:
                    logger.exception("heartbeat_failed run_id=%s", run["id"])
                    return

        pulse = threading.Thread(target=heartbeat, daemon=True)
        pulse.start()
        try:
            state = ResearchWorkflow(self.store, self.settings, run).invoke()
            self.store.finish(run["id"], run["lease_token"], result=state["report"])
        except LeaseLost:
            logger.warning("lease_lost run_id=%s", run["id"])
        except InterruptedError:
            try:
                self.store.finish(run["id"], run["lease_token"])
            except LeaseLost:
                pass
        except Exception as exc:
            # Store only safe exception type, not prompts, provider bodies, secrets or PDF text.
            logger.error("run_failed run_id=%s error_type=%s", run["id"], type(exc).__name__)
            try:
                self.store.finish(run["id"], run["lease_token"], error=type(exc).__name__)
            except LeaseLost:
                pass
        finally:
            stop.set()
            pulse.join(timeout=2)
        return True

    def serve(self, stop: threading.Event | None = None):
        stop = stop or threading.Event()
        while not stop.is_set():
            if not self.once():
                stop.wait(0.3)
