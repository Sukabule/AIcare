"""Optional graph-level tracing. No node decorators and no cloud endpoint defaults."""

import asyncio
import logging
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


class Observability:
    def __init__(self, settings):
        self.settings, self.client, self.handler = settings, None, None
        self._request_io = ContextVar("langfuse-request-io", default=None)

    @property
    def enabled(self):
        return bool(
            self.settings.langfuse_public_key
            and self.settings.langfuse_secret_key.get_secret_value()
            and self.settings.langfuse_base_url
        )

    def bind(self, graph):
        if not self.enabled:
            return graph
        try:
            from langfuse import Langfuse
            from langfuse.langchain import CallbackHandler

            self.client = Langfuse(
                public_key=self.settings.langfuse_public_key,
                secret_key=self.settings.langfuse_secret_key.get_secret_value(),
                base_url=self.settings.langfuse_base_url,
                timeout=3,
                environment="local",
                flush_at=10,
                flush_interval=2,
            )
            self.handler = CallbackHandler(public_key=self.settings.langfuse_public_key)
            return graph.with_config({"callbacks": [self.handler]})
        except Exception:
            logger.exception("Langfuse initialization skipped")
            return graph

    @contextmanager
    def request_scope(self, cid, *, trace_id=None, name="customer-turn", input=None):
        # LangChain callbacks run in their own context. A single request boundary
        # gives async graph nodes a public, request-local OTel parent as well.
        scope, request_trace = ExitStack(), None
        token = self._request_io.set(None)
        try:
            if self.client is None:
                yield None
                return
            try:
                from langfuse import propagate_attributes

                span = scope.enter_context(
                    self.client.start_as_current_observation(
                        name=name, trace_context={"trace_id": trace_id} if trace_id else None
                    )
                )
                scope.enter_context(propagate_attributes(session_id=str(cid)))
                request_trace = span.trace_id
                self._request_io.set((span, input))
                if input is not None:
                    self.record_output(None)
            except Exception:
                logger.warning("Langfuse request setup skipped", exc_info=True)
            try:
                yield request_trace
            except BaseException as exc:
                self.record_output({"error": type(exc).__name__}, error=type(exc).__name__)
                raise
        finally:
            self._request_io.reset(token)
            try:
                scope.close()
            except Exception:
                logger.warning("Langfuse request cleanup skipped", exc_info=True)

    def record_output(self, value, *, error=None):
        current = self._request_io.get()
        if current is None:
            return
        span, payload = current
        try:
            status = {"level": "ERROR", "status_message": error} if error else {}
            span.update(input=payload, output=value, **status)
            if payload is not None:
                # SDK4's public compatibility method is needed by the pinned v3 UI.
                # Summary scopes have no request payload and never replace chat I/O.
                span.set_trace_io(input=payload, output=value)
        except Exception:
            logger.warning("Langfuse request I/O update skipped", exc_info=True)

    def current_trace_id(self):
        try:
            return self.client.get_current_trace_id() if self.client else None
        except Exception:
            return None

    @contextmanager
    def summary_scope(self, cid, trace_id):
        with self.request_scope(cid, trace_id=trace_id, name="async-summary"):
            yield (
                {"callbacks": [self.handler], "metadata": {"langfuse_session_id": str(cid)}}
                if self.handler
                else None
            )

    async def intent(self, intent, confidence, cid):
        if self.client is None:
            return
        try:
            trace_id = self.client.get_current_trace_id()
            if not trace_id:
                return
            # Public v3 ingestion upserts trace metadata. SDK4 no longer exposes
            # update_current_trace, while the v3 server supports this same API.
            from langfuse.api.ingestion.types.ingestion_event import IngestionEvent_TraceCreate
            from langfuse.api.ingestion.types.trace_body import TraceBody

            event = IngestionEvent_TraceCreate(
                id=str(uuid4()),
                timestamp=datetime.now(UTC).isoformat(),
                type="trace-create",
                body=TraceBody(
                    id=trace_id,
                    session_id=str(cid),
                    metadata={"intent": intent, "intent_confidence": confidence},
                    tags=["intent:" + intent],
                ),
            )
            await asyncio.to_thread(
                self.client.api.ingestion.batch,
                batch=[event],
                request_options={"timeout_in_seconds": 3, "max_retries": 0},
            )
            return trace_id
        except Exception:
            logger.warning("Langfuse intent update skipped", exc_info=True)

    async def flush(self):
        if self.client:
            await asyncio.to_thread(self.client.flush)
