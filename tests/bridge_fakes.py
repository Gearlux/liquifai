"""Hand-rolled fake SDK for the liquifai.bridge tests.

Deliberately NOT MagicMock — these fakes define the bridge's connection
contract (see liquifai.bridge.BridgeConnection) instead of echoing whatever is
asked of them: a FakeSub records every call and returns canned results, a
FakeClient holds sub-clients as attributes, and a FakeConn counts get_client()
calls so tests can assert dry-run never touches the client.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple


class FakeSub:
    """A fake SDK sub-client: records calls, returns canned per-method results."""

    def __init__(self, results: Optional[Dict[str, Any]] = None) -> None:
        self.calls: List[Tuple[str, tuple, dict]] = []
        self.results = dict(results or {})

    def __getattr__(self, method: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((method, args, kwargs))
            result = self.results.get(method)
            return result(*args, **kwargs) if callable(result) else result

        return call

    def last_call(self) -> Tuple[str, tuple, dict]:
        return self.calls[-1]


class FakeClient:
    """A fake SDK client whose sub-clients are plain attributes."""

    def __init__(self, **subs: Any) -> None:
        for name, sub in subs.items():
            setattr(self, name, sub)

    def __getattr__(self, name: str) -> Any:
        # Dynamic sub-client attributes (set in __init__); declaring __getattr__
        # tells mypy attribute access like `client.widget` is legal on the fake.
        raise AttributeError(name)


class FakeConn:
    """Satisfies the BridgeConnection contract; counts get_client() calls."""

    def __init__(self, client: Optional[FakeClient] = None, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.client = client if client is not None else FakeClient()
        self.get_client_calls = 0

    def get_client(self) -> FakeClient:
        self.get_client_calls += 1
        return self.client


class BaseWidgetClient:
    """A fake SDK base class carrying the docstrings @expose stubs inherit."""

    def list_widgets(self) -> None:
        """List every widget on the platform."""

    def get_widget(self, name: str) -> None:
        """Get one widget by name."""

    def delete_widget(self, name: str) -> None:
        """Delete a widget."""

    def get_widget_versions(self, name: str) -> None:
        """List a widget's versions."""
