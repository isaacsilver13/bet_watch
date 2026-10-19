from fastapi.testclient import TestClient as BaseTestClient


class TestClient(BaseTestClient):
    """The normal supported ingress is a direct local browser or CLI."""

    __test__ = False

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("client", ("127.0.0.1", 50000))
        kwargs.setdefault("base_url", "http://localhost")
        super().__init__(*args, **kwargs)
