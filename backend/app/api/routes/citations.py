from fastapi import APIRouter

from app.data.mock_responses import CITATIONS
from app.models.schemas import Citation

router = APIRouter()


@router.get("/api/citations", response_model=dict[str, Citation])
async def list_citations() -> dict[str, Citation]:
    """Full citation library, independent of any conversation.

    Lets the sidebar's statute browser preview a section before any
    question has been asked.
    """
    return CITATIONS
