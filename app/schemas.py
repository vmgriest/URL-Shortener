from datetime import datetime
from pydantic import BaseModel, HttpUrl, field_validator


class ShortenRequest(BaseModel):
    url: HttpUrl

    @field_validator("url")
    @classmethod
    def url_must_have_scheme(cls, v: HttpUrl) -> HttpUrl:
        if v.scheme not in ("http", "https"):
            raise ValueError("URL must use http or https")
        return v


class ShortenResponse(BaseModel):
    short_code: str
    short_url: str
    original_url: str


class URLInfo(BaseModel):
    short_code: str
    original_url: str
    created_at: datetime
    hit_count: int

    model_config = {"from_attributes": True}
