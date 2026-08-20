import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Pile(Base):
    __tablename__ = "piles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    documents = relationship("Document", back_populates="pile")
    runs = relationship("Run", back_populates="pile")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pile_id: Mapped[str] = mapped_column(ForeignKey("piles.id"))
    filename: Mapped[str] = mapped_column(String(400))
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    pile = relationship("Pile", back_populates="documents")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pile_id: Mapped[str] = mapped_column(ForeignKey("piles.id"))
    status: Mapped[str] = mapped_column(String(40), default="pending")
    current_stage: Mapped[str] = mapped_column(String(40), default="ingest")
    kill_requested: Mapped[int] = mapped_column(Integer, default=0)
    trigger: Mapped[str] = mapped_column(String(40), default="full")
    new_document_ids: Mapped[str] = mapped_column(Text, default="[]")
    playbook_json: Mapped[str] = mapped_column(Text, default="{}")
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    cost_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    pile = relationship("Pile", back_populates="runs")
    checkpoints = relationship("Checkpoint", back_populates="run")
    review_items = relationship("ReviewItem", back_populates="run")
    events = relationship("Event", back_populates="run")


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    __table_args__ = (UniqueConstraint("run_id", "stage", name="uq_run_stage"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    stage: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run = relationship("Run", back_populates="checkpoints")


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    item_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(400))
    body: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")

    run = relationship("Run", back_populates="review_items")


class RegisterSection(Base):
    __tablename__ = "register_sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    section_id: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    source_ids: Mapped[str] = mapped_column(Text, default="[]")
    content_hash: Mapped[str] = mapped_column(String(64))
    unchanged: Mapped[int] = mapped_column(Integer, default=0)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    stage: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run = relationship("Run", back_populates="events")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    pile_id: Mapped[str] = mapped_column(ForeignKey("piles.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[str] = mapped_column(Text, default="[]")


class StageCost(Base):
    __tablename__ = "stage_costs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    stage: Mapped[str] = mapped_column(String(40))
    ms: Mapped[float] = mapped_column(Float, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float] = mapped_column(Float, default=0)
