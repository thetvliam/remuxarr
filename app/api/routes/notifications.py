from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.models import NotificationState
from app.database.session import get_db

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/state")
def get_notification_state(db: Session = Depends(get_db)):
    """
    Current state of the email consecutive-failure circuit breaker.
    Polled by the Email settings section to show a banner when tripped.
    """
    state = db.get(NotificationState, 1)
    if state is None:
        return {"tripped": False, "consecutive_failures": 0}
    return {
        "tripped":              state.breaker_tripped,
        "consecutive_failures": state.consecutive_failures,
    }
