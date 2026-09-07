from typing import Literal

from pydantic import BaseModel


SupportedLanguage = Literal["vi", "en", "ja"]


class DefaultLanguageUpdate(BaseModel):
    default_language: SupportedLanguage

