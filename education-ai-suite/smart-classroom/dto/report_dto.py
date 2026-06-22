from pydantic import BaseModel
from typing import Optional


class ReportRequest(BaseModel):
    session_id: str