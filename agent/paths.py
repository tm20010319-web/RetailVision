"""Repository defaults and bounded compatibility for relocated SQLite files."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_DIRECTORY = PROJECT_ROOT / "runtime" / "databases"
MODEL_DIRECTORY = PROJECT_ROOT / "data" / "models"
_RELOCATED_DATABASES = frozenset({
    "retailvision.db", "demo_flow.db", "retailvision_test.db", "ui-verify-20260731-1445.db",
})


def default_database_url() -> str:
    return "sqlite:///" + (DATABASE_DIRECTORY / "retailvision.db").as_posix()


def resolve_database_url(value: str) -> str:
    """Redirect known former root files, preserving other configured URLs.

    Existing old files win. Compatibility is local to this checkout and
    never selects a corresponding database in another worktree.
    """
    from sqlalchemy.engine import make_url

    url = make_url(value)
    if url.get_backend_name() != "sqlite" or not url.database or url.query:
        return value
    source = Path(url.database)
    if source.name not in _RELOCATED_DATABASES:
        return value
    source = source.resolve()
    destination = DATABASE_DIRECTORY / source.name
    if source.parent == PROJECT_ROOT and not source.exists() and destination.is_file():
        return url.set(database=destination.as_posix()).render_as_string(hide_password=False)
    return value
