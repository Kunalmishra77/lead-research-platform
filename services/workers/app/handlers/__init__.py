"""Job handlers. `build_registry()` wires every handler module into one registry."""

from app.jobs.registry import HandlerRegistry


def build_registry() -> HandlerRegistry:
    registry = HandlerRegistry()
    # Handler modules register themselves here as they are added (system.ping in task 1.10).
    return registry
