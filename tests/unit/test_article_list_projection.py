"""The list endpoint returns a summary shape, not whole articles."""

from datetime import datetime

from sqlalchemy.dialects import mysql

from app.repositories.article_repo import build_list_query
from app.schemas.article import ArticleListOut, ArticleOut, ArticleSummaryOut

SUMMARY_FIELDS = {"id", "author_id", "title", "created_at", "updated_at"}


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=mysql.dialect()))


# --- the response shape ---------------------------------------------------------------------


def test_summary_carries_every_field_except_the_body():
    assert set(ArticleSummaryOut.model_fields) == SUMMARY_FIELDS


def test_summary_has_no_body_field():
    assert "body" not in ArticleSummaryOut.model_fields


def test_detail_still_carries_the_body():
    """The projection is a list-only change. A reader who wants the text asks for one article."""
    assert "body" in ArticleOut.model_fields


def test_list_page_holds_summaries():
    assert ArticleListOut.model_fields["items"].annotation == list[ArticleSummaryOut]


def test_summary_validates_from_an_orm_row():
    """`from_attributes` is what lets the service pass a SQLAlchemy Row straight in."""
    assert ArticleSummaryOut.model_config["from_attributes"] is True


def test_summary_ignores_a_body_it_is_given():
    """A list page cached before the projection still decodes, minus the text it carried."""
    summary = ArticleSummaryOut.model_validate(
        {
            "id": 1,
            "author_id": 2,
            "title": "t",
            "body": "the text an older schema wrote",
            "created_at": "2026-08-22T12:00:00",
            "updated_at": "2026-08-22T12:00:00",
        }
    )
    assert not hasattr(summary, "body")
    assert summary.id == 1


# --- the SQL --------------------------------------------------------------------------------


def test_list_query_does_not_select_the_body_column():
    """The point of the change. MySQL reads no `MEDIUMTEXT` for a list page."""
    assert "articles.body" not in _compiled(build_list_query(limit=21))


def test_list_query_selects_every_summary_column():
    sql = _compiled(build_list_query(limit=21))
    for field in SUMMARY_FIELDS:
        assert f"articles.{field}" in sql


def test_paged_list_query_does_not_select_the_body_column():
    after = (datetime(2026, 8, 22, 12, 0, 0), 500)
    assert "articles.body" not in _compiled(build_list_query(limit=21, after=after))


def test_author_filtered_list_query_does_not_select_the_body_column():
    assert "articles.body" not in _compiled(build_list_query(limit=21, author_id=7))


def test_list_query_still_filters_soft_deleted_rows():
    """The projection must not disturb the soft-delete chokepoint."""
    assert "articles.deleted_at IS NULL" in _compiled(build_list_query(limit=21))


def test_list_query_still_orders_by_the_keyset():
    sql = _compiled(build_list_query(limit=21))
    assert "ORDER BY articles.created_at DESC, articles.id DESC" in sql
