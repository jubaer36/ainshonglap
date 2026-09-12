from fastapi import APIRouter

from app.data.mock_responses import get_response
from app.models.schemas import AssistantMessage, QueryRequest

router = APIRouter()


@router.post("/api/query", response_model=AssistantMessage)
async def query(payload: QueryRequest) -> AssistantMessage:
    return get_response(payload.question)
