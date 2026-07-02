from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.models import PlexAnalyzeBacklog
from app.database.session import get_db

router = APIRouter(prefix="/api/plex", tags=["plex"])


@router.get("/backlog")
def get_backlog_count(db: Session = Depends(get_db)):
    """
    Number of files currently queued awaiting an explicit Plex Analyze call.
    Polled by the Plex settings section to show queue size.
    """
    count = db.query(PlexAnalyzeBacklog).count()
    return {"count": count}
