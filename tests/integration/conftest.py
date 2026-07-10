import contextlib
import os

import pytest

from knowledge_base.arango import ArangoClient, ArangoError
from knowledge_base.config import load_settings

_ISOLATED_DATABASE = "knowledge_base_integration_test"


@pytest.fixture(scope="session", autouse=True)
def _isolated_integration_database() -> None:
    """Run integration tests against a dedicated, freshly-reset database.

    These tests bootstrap schema and upsert documents/chunks/edges. Running them against the
    default `knowledge_base` would seed synthetic test data into a real personal corpus, and
    reusing a database across runs lets one run's leftovers contaminate the next. So, unless the
    operator pinned KB_ARANGO_DATABASE explicitly (e.g. a CI service container), point the tests at
    a throwaway database and drop it at session start so every run is clean — the way CI's fresh
    container already behaves.
    """
    if os.getenv("KB_RUN_INTEGRATION") != "1" or os.getenv("KB_ARANGO_DATABASE") is not None:
        return
    os.environ["KB_ARANGO_DATABASE"] = _ISOLATED_DATABASE
    client = ArangoClient(load_settings())
    with contextlib.suppress(ArangoError):
        # Drop a leftover database from a previous run; the tests bootstrap the schema either way.
        client.request("DELETE", f"/_api/database/{_ISOLATED_DATABASE}", expected=(200, 404))
