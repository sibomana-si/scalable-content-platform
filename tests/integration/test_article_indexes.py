"""Index-usage regression tests.

Runs 'EXPLAIN FORMAT=JSON' over the 'production' list SQL, compiled from
'build_list_query' with literal binds, so these plans are the queries the app runs,
not lookalikes. Asserts the two composite indexes carry the hot read paths with no
filesort. The dataset is seeded large enough (and ANALYZEd) that the optimizer has no
excuse to ignore an index.
"""

import json
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import insert, text
from sqlalchemy.dialects import mysql

from app.models import Article
from app.repositories.article_repo import build_list_query

pytestmark = pytest.mark.integration

T0 = datetime(2026, 1, 1, 8, 0, 0)
LIST_INDEX = "idx_articles_deleted_created"
AUTHOR_INDEX = "idx_articles_author_deleted_created"


@pytest_asyncio.fixture
async def seeded(db_session, user_factory) -> dict:
    """~500 articles over 5 authors, ~10% soft-deleted, spread of created_at."""

    authors = [await user_factory() for _ in range(5)]
    rows = [
        {
            "author_id": authors[i % len(authors)].id,
            "title": f"seed article {i}",
            "body": "seed body",
            "created_at": T0 + timedelta(minutes=i),
            "updated_at": T0 + timedelta(minutes=i),
            "deleted_at": (T0 + timedelta(days=30)) if i % 10 == 0 else None,
        }
        for i in range(500)
    ]
    await db_session.execute(insert(Article), rows)
    await db_session.commit()
    await db_session.execute(text("ANALYZE TABLE articles"))
    return {"author_id": authors[0].id}


async def _explain(db_session, stmt) -> str:
    sql = str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))
    raw = (await db_session.execute(text(f"EXPLAIN FORMAT=JSON {sql}"))).scalar_one()
    return json.dumps(json.loads(raw))  # normalized whitespace for string assertions


def _assert_uses_index(plan: str, index_name: str) -> None:
    assert f'"key": "{index_name}"' in plan, f"expected {index_name} in plan: {plan}"
    assert '"using_filesort": true' not in plan, f"filesort in plan: {plan}"


async def test_public_list_uses_deleted_created_index(db_session, seeded):
    plan = await _explain(db_session, build_list_query(limit=21))
    _assert_uses_index(plan, LIST_INDEX)


async def test_public_list_continuation_uses_deleted_created_index(db_session, seeded):
    after = (T0 + timedelta(minutes=250), 250)
    plan = await _explain(db_session, build_list_query(limit=21, after=after))
    _assert_uses_index(plan, LIST_INDEX)


async def test_author_filter_uses_author_deleted_created_index(db_session, seeded):
    plan = await _explain(db_session, build_list_query(limit=21, author_id=seeded["author_id"]))
    _assert_uses_index(plan, AUTHOR_INDEX)


async def test_author_filter_continuation_uses_author_index(db_session, seeded):
    after = (T0 + timedelta(minutes=250), 250)
    plan = await _explain(
        db_session, build_list_query(limit=21, after=after, author_id=seeded["author_id"])
    )
    _assert_uses_index(plan, AUTHOR_INDEX)
