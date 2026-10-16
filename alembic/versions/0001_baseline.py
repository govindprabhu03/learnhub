"""baseline schema (SQLite and Postgres+pgvector)

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMB_DIM = 384  # BAAI/bge-small-en-v1.5; switching to a model with another dimension needs a new migration


def _id():
    return sa.Column("id", sa.Integer, primary_key=True)


def _created():
    return sa.Column("created", sa.Text, server_default=sa.text("CURRENT_TIMESTAMP"))


def _int(name, default=None, nullable=True):
    return sa.Column(name, sa.Integer, nullable=nullable, server_default=None if default is None else sa.text(str(default)))


def _fk(table):
    return sa.ForeignKey(f"{table}.id", ondelete="CASCADE")


def upgrade():
    pg = op.get_bind().dialect.name == "postgresql"
    if pg:
        from pgvector.sqlalchemy import Vector
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        emb = sa.Column("emb", Vector(EMB_DIM))
    else:
        emb = sa.Column("emb", sa.LargeBinary)
    T, I = sa.Text, sa.Integer

    op.create_table("users", _id(), sa.Column("email", T, nullable=False, unique=True), sa.Column("name", T, nullable=False),
                    sa.Column("pw", T, nullable=False), _created())
    op.create_table("subjects", _id(), sa.Column("user_id", I, _fk("users"), nullable=False), sa.Column("name", T, nullable=False),
                    sa.Column("emoji", T, server_default="📘"), _created())
    op.create_table("documents", _id(), sa.Column("user_id", I, _fk("users"), nullable=False),
                    sa.Column("subject_id", I, _fk("subjects"), nullable=False), sa.Column("filename", T, nullable=False),
                    sa.Column("path", T, nullable=False), sa.Column("status", T, server_default="processing"), sa.Column("error", T),
                    _int("pages", 0), sa.Column("summary", T), sa.Column("concepts", T, server_default="[]"),
                    sa.Column("important_qs", T, server_default="[]"), _created())
    op.create_table("chunks", _id(), _int("user_id", nullable=False), _int("subject_id", nullable=False),
                    sa.Column("document_id", I, _fk("documents"), nullable=False), _int("idx"), _int("page"),
                    sa.Column("text", T, nullable=False), emb, sa.Column("emb_model", T))
    op.create_index("ix_chunks", "chunks", ["user_id", "subject_id"])
    op.create_table("questions", _id(), _int("user_id", nullable=False), _int("subject_id", nullable=False),
                    sa.Column("document_id", I, _fk("documents")), sa.Column("topic", T, nullable=False),
                    sa.Column("question", T, nullable=False), sa.Column("options", T, nullable=False),
                    _int("answer", nullable=False), sa.Column("explanation", T), sa.Column("difficulty", T, server_default="medium"))
    op.create_index("ix_questions", "questions", ["user_id", "subject_id"])
    op.create_table("attempts", _id(), _int("user_id", nullable=False), sa.Column("question_id", I, _fk("questions"), nullable=False),
                    _int("subject_id", nullable=False), sa.Column("topic", T, nullable=False), _int("correct", nullable=False),
                    sa.Column("ts", T, nullable=False))
    op.create_index("ix_attempts", "attempts", ["user_id", "subject_id"])
    op.create_table("flashcards", _id(), _int("user_id", nullable=False), _int("subject_id", nullable=False),
                    sa.Column("document_id", I, _fk("documents")), sa.Column("topic", T), sa.Column("front", T, nullable=False),
                    sa.Column("back", T, nullable=False), sa.Column("ease", sa.Float, server_default=sa.text("2.5")),
                    _int("interval", 0), _int("reps", 0), sa.Column("due", T, nullable=False))
    op.create_index("ix_cards", "flashcards", ["user_id", "subject_id", "due"])
    op.create_table("reviews", _id(), _int("user_id", nullable=False), _int("card_id", nullable=False),
                    sa.Column("grade", T), sa.Column("ts", T, nullable=False))
    op.create_table("messages", _id(), _int("user_id", nullable=False), _int("subject_id", nullable=False),
                    sa.Column("role", T, nullable=False), sa.Column("content", T, nullable=False), sa.Column("sources", T),
                    sa.Column("ts", T, server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_messages", "messages", ["user_id", "subject_id"])
    op.create_table("plans", _id(), _int("user_id", nullable=False), sa.Column("subject_id", I, _fk("subjects"), nullable=False),
                    sa.Column("exam_date", T, nullable=False), _int("daily_minutes", nullable=False),
                    sa.Column("start_time", T, nullable=False), sa.Column("last_replan", T), _int("dropped", 0), _created())
    op.create_table("plan_tasks", _id(), sa.Column("plan_id", I, _fk("plans"), nullable=False), _int("user_id", nullable=False),
                    sa.Column("day", T, nullable=False), sa.Column("start", T, nullable=False), _int("minutes", nullable=False),
                    sa.Column("kind", T, nullable=False), sa.Column("topic", T, server_default=""),
                    sa.Column("label", T, nullable=False), _int("done", 0))
    op.create_index("ix_tasks", "plan_tasks", ["plan_id", "day"])


def downgrade():
    for t in ("plan_tasks", "plans", "messages", "reviews", "flashcards", "attempts", "questions", "chunks",
              "documents", "subjects", "users"):
        op.drop_table(t)
