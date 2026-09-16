# Old db scheme, don't remove
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    discord_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(80))
    global_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    avatar_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_moderator: Mapped[bool] = mapped_column(default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    runs: Mapped[list["Run"]] = relationship(
        back_populates="runner",
        foreign_keys="Run.user_id",
        cascade="all, delete-orphan",
    )
    profile: Mapped["UserProfile | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )

    @property
    def display_name(self) -> str:
        return self.global_name or self.username or self.discord_id


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bio: Mapped[str] = mapped_column(Text, default="")
    background_color: Mapped[str] = mapped_column(String(20), default="slate")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="profile")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    short_name: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(String(240), default="")
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    build_slug: Mapped[str] = mapped_column(String(80), default="", index=True)
    build_name: Mapped[str] = mapped_column(String(120), default="")
    build_order: Mapped[int] = mapped_column(Integer, default=0)
    rules_file: Mapped[str] = mapped_column(String(240), default="")

    runs: Mapped[list["Run"]] = relationship(back_populates="category")

    @property
    def display_name(self) -> str:
        return self.short_name or self.name


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("time_ms > 0", name="ck_runs_positive_time"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_runs_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    time_ms: Mapped[int] = mapped_column(Integer, index=True)
    video_url: Mapped[str] = mapped_column(String(500))
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    runner: Mapped[User] = relationship(back_populates="runs", foreign_keys=[user_id])
    category: Mapped[Category] = relationship(back_populates="runs")
    reviewed_by: Mapped[User | None] = relationship(foreign_keys=[reviewed_by_user_id])


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    actor_discord_id: Mapped[str] = mapped_column(String(32))
    actor_name: Mapped[str] = mapped_column(String(80))
    runner_discord_id: Mapped[str] = mapped_column(String(32))
    runner_name: Mapped[str] = mapped_column(String(80))
    category_name: Mapped[str] = mapped_column(String(240))
    time_ms: Mapped[int] = mapped_column(Integer)
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
