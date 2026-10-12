"""What a worker is allowed to import at startup.

This exists because of a production outage. `app/places/__init__.py` re-exported the ingest
builders for convenience, so importing anything from the package -- which the connector does, for
a dataclass -- pulled in `osmium`. Its wheel links against `libexpat`, which `python:3.12-slim`
does not carry, and every worker crash-looped on `ImportError: libexpat.so.1`. No job ran at all.

The 881 tests passed throughout. They run on a machine where the library happens to exist, which
is the whole reason an import test has to block the module rather than trust the environment.

The rule: the serving path imports what it needs to answer jobs. A library needed once a month by
an operator's shell command is not that, and the cost of being wrong is the whole product down
rather than one feature degraded.
"""

import subprocess
import sys

import pytest

#: Heavy, and needed only by the monthly index build: `osmium` reads a 1.6 GB PBF and `duckdb`
#: reads Overture's GeoParquet. Neither has any business in a process whose job is to consume
#: Redis streams.
INGEST_ONLY = ("osmium", "duckdb")

#: Every module a worker loads on its way to serving a job.
SERVING_IMPORTS = (
    "app.main",
    "app.connectors.factory",
    "app.connectors.places",
    "app.places",
    "app.places.index",
    "app.places.model",
    "app.handlers.discovery",
)

_BLOCKER = """
import sys


class Blocked:
    \"\"\"Refuses the ingest-only modules, so an import fails here, not in production.\"\"\"

    def find_module(self, name, path=None):
        return self.find_spec(name, path)

    def find_spec(self, name, path=None, target=None):
        root = name.split(".")[0]
        if root in {blocked!r}:
            raise ImportError(
                f"{{name}} is not importable on the serving path; "
                "import it inside the command that builds an index"
            )
        return None


sys.meta_path.insert(0, Blocked())
import {module}
"""


@pytest.mark.parametrize("module", SERVING_IMPORTS)
def test_the_serving_path_does_not_need_the_ingest_libraries(module: str) -> None:
    """Imports `module` in a subprocess where osmium and duckdb cannot be imported.

    A subprocess, because this test session has already imported both for the ingest tests and
    `sys.modules` would hand them over however hard the finder tried to refuse.
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _BLOCKER.format(blocked=INGEST_ONLY, module=module)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"importing {module} reaches an ingest-only library:\n{result.stderr}"
    )


def test_the_builders_are_still_reachable_where_they_belong() -> None:
    """The other half: narrowing the package must not have made an index unbuildable."""
    from app.places.osm import build_index as build_osm  # noqa: PLC0415
    from app.places.overture import build_index as build_overture  # noqa: PLC0415

    assert callable(build_osm)
    assert callable(build_overture)


def test_the_operator_command_still_runs() -> None:
    """`python -m app.places --help` is how an index gets built; it may import anything."""
    result = subprocess.run(
        [sys.executable, "-m", "app.places", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "build-overture" in result.stdout


def test_the_blocker_actually_blocks() -> None:
    """Proves the tests above are not vacuous.

    A guard that passes because it never refused anything is worse than no guard: it reports
    the property it was written to protect as holding, every time, whatever the code does. So
    this asserts the one import that *should* fail under the blocker does.
    """
    result = subprocess.run(  # noqa: S603 -- our own interpreter, our own source
        [sys.executable, "-c", _BLOCKER.format(blocked=INGEST_ONLY, module="app.places.osm")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "not importable on the serving path" in result.stderr
