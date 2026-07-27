from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Article(Base):
    __tablename__ = "articles"
    # Composite list indexes: MySQL has no partial indexes, so
    # deleted_at is a leading key column instead of a WHERE clause; created_at follows
    # for the keyset order, and InnoDB's implicit PK suffix supplies the id tiebreaker.
    __table_args__ = (
        Index("idx_articles_deleted_created", "deleted_at", "created_at"),
        Index("idx_articles_author_deleted_created", "author_id", "deleted_at", "created_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    author_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(MEDIUMTEXT)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), default=None)
