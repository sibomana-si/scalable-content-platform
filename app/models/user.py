from datetime import datetime

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, TINYINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.role import Role


class User(Base):
    __tablename__ = "users"
    __table_args__ = {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_0900_ai_ci"
    }

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        ForeignKey("roles.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")
    )

    # Eager join: authorization needs the role name on every authenticated request,
    # and lazy loading is unavailable under the async session.
    role: Mapped[Role] = relationship(lazy="joined")

    @property
    def is_admin(self) -> bool:
        return self.role.name == "admin"
