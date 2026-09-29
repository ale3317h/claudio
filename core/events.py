import asyncio
import inspect
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List

import structlog

logger = structlog.get_logger(__name__)

EventHandler = Callable[..., Awaitable[None]]


class EventBus:
    def __init__(self):
        self._handlers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._middleware: List[EventHandler] = []

    def on(self, event_name: str) -> Callable[[EventHandler], EventHandler]:
        def decorator(handler: EventHandler) -> EventHandler:
            self.subscribe(event_name, handler)
            return handler

        return decorator

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(f"Handler for {event_name} must be async")
        self._handlers[event_name].append(handler)
        logger.debug("event_subscribed", event_name=event_name, handler=handler.__name__)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        if handler in self._handlers[event_name]:
            self._handlers[event_name].remove(handler)

    def add_middleware(self, middleware: EventHandler) -> None:
        self._middleware.append(middleware)

    async def emit(self, event_name: str, *args: Any, **kwargs: Any) -> None:
        for middleware in self._middleware:
            await middleware(event_name, *args, **kwargs)

        handlers = self._handlers.get(event_name, [])
        if not handlers:
            logger.debug("event_no_handlers", event_name=event_name)
            return

        tasks = [handler(*args, **kwargs) for handler in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for handler, result in zip(handlers, results, strict=True):
            if isinstance(result, Exception):
                logger.error(
                    "event_handler_error",
                    event_name=event_name,
                    handler=handler.__name__,
                    error=str(result),
                    exc_info=result,
                )


event_bus = EventBus()


async def emit_event(event_name: str, *args: Any, **kwargs: Any) -> None:
    await event_bus.emit(event_name, *args, **kwargs)


def event_handler(event_name: str) -> Callable[[EventHandler], EventHandler]:
    return event_bus.on(event_name)