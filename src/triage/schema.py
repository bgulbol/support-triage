"""The triage 'form' the model fills in. One Pydantic model is the single source of truth."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["licensing", "installation", "calculation", "billing", "bug", "feature_request", "other"]
Urgency = Literal["low", "medium", "high"]
Language = Literal["tr", "en"]


class TriageResult(BaseModel):
    language: Language = Field(description="Language of the customer's message")
    category: Category = Field(description="Main topic of the message")
    urgency: Urgency = Field(description="How urgent the message is")
    needs_human: bool = Field(description="True if the SolarDesk team has to act or the answer is not covered")
    reason: str = Field(description="One short sentence explaining the classification")
    reply: str = Field(description="The message to send to the customer, in the customer's language")

    @field_validator("language", "category", "urgency", mode="before")
    @classmethod
    def lowercase(cls, value):
        # The docs warn that enum values can come back with different capitalisation.
        return value.lower() if isinstance(value, str) else value