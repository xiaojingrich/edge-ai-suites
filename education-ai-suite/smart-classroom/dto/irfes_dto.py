from pydantic import BaseModel


class IRFESRequest(BaseModel):
    session_id: str
