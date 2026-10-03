"""Request models shared by the authenticated portal and Assist API."""

from pydantic import BaseModel, Field


class ResolveInput(BaseModel):
    complaint: str = Field(min_length=5, max_length=5000)
    product_hint: str | None = Field(default=None, max_length=100)
