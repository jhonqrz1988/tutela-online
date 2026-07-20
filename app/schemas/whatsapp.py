from pydantic import BaseModel


class WebhookTwilio(BaseModel):
    Body: str | None = None
    From: str | None = None
    MessageSid: str | None = None
    MediaUrl0: str | None = None
    MediaContentType0: str | None = None
    NumMedia: str = "0"
