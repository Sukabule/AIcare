import logging

from fastapi import APIRouter, HTTPException, Request

from app.core.errors import public_error
from app.core.prompts import EXTRACT_PROMPT
from app.schemas import AfterSalesTicket, ExtractRequest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/extract", response_model=AfterSalesTicket)
async def extract(body: ExtractRequest, request: Request) -> AfterSalesTicket:
    try:
        model = request.app.state.llm
        if model is None:
            model = request.app.state.model_factory()
        structured = model.with_structured_output(
            AfterSalesTicket, method="json_schema", strict=True
        )
        result = await structured.ainvoke(EXTRACT_PROMPT.format_messages(text=body.text))
        return AfterSalesTicket.model_validate(result)
    except Exception as exc:
        logger.warning("Extraction failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=public_error(exc)) from exc
