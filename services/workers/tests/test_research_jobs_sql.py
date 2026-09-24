"""The statements that update a research job's run state (app/db/research_jobs.py).

These exist because the executor's own tests use a fake repo, so no test ever put this SQL in
front of a database. A live Delhi run did, and found that every one of seventeen tasks failed
*after* storing its leads and charging for them: the counters were written as `:name::bigint`,
which SQLAlchemy refuses to bind because it cannot tell that colon from PostgreSQL's `::` cast.
The placeholder reached Postgres verbatim and the statement was rejected.

A compile is enough to catch it, and costs nothing.
"""

import pytest
from sqlalchemy import text

from app.db.research_jobs import PROGRESS_COUNTERS, progress_sql
from app.jobs.errors import InvalidInputError


def bound(sql: str) -> set[str]:
    """Every parameter SQLAlchemy actually recognises in a statement."""
    return set(text(sql).compile().params)


def test_every_counter_reaches_the_database_as_a_bound_parameter() -> None:
    counts = {"candidates": 12, "leads": 5, "values": 60}

    # The bug in one line: `:leads::bigint` compiles to zero parameters, so `leads` never
    # reaches Postgres and the statement is a syntax error.
    assert bound(progress_sql(counts)) == {"id", *counts}


@pytest.mark.parametrize("counter", sorted(PROGRESS_COUNTERS))
def test_each_counter_on_its_own_also_binds(counter: str) -> None:
    assert bound(progress_sql({counter: 1})) == {"id", counter}


def test_the_statement_never_interpolates_a_name_it_does_not_know() -> None:
    # The counter names are interpolated into the SQL rather than bound, because they are column
    # keys inside a jsonb object. Checked against a closed set rather than escaped: a new counter
    # should be a deliberate addition, not whatever a caller happened to pass.
    with pytest.raises(InvalidInputError, match="unknown progress counters"):
        progress_sql({"leads": 1, "'; drop table app.research_jobs; --": 1})


def test_a_finished_job_is_never_reopened_by_a_late_task() -> None:
    sql = progress_sql({"leads": 1})
    # A task that finishes after the user cancelled, or after the job already failed, must not
    # quietly add to its counters and make it look alive.
    assert "status not in ('completed', 'failed', 'cancelled')" in sql


def test_counters_add_rather_than_overwrite() -> None:
    sql = progress_sql({"leads": 1})
    # A job's tasks run in parallel across workers and each knows only its own share, so an
    # assignment would leave the counter showing whichever task finished last.
    assert "coalesce(cast(progress->>'leads' as bigint), 0) + cast(:leads as bigint)" in sql
