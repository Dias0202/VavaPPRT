"""Modelos de entrada/saída da API (Pydantic)."""
from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    riot_id: str = Field(min_length=1, max_length=32)
    tagline: str = Field(min_length=1, max_length=10)
    region: str = "latam"


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class MeOut(BaseModel):
    id: int
    username: str
    riot_id: str
    tagline: str
    region: str
    is_admin: bool


class AddPlayerIn(BaseModel):
    riot_id: str = Field(min_length=1, max_length=32)
    tagline: str = Field(min_length=1, max_length=10)
    region: str = "latam"


class MemberRef(BaseModel):
    riot_id: str
    tagline: str


class GroupDetailIn(BaseModel):
    members: list[MemberRef] = Field(min_length=2, max_length=5)


class GoalIn(BaseModel):
    """Meta de treino. `metric` precisa ser uma das que a plataforma mede."""
    metric: str = Field(min_length=1, max_length=32)
    target: float
    baseline: float | None = None
    label: str | None = Field(default=None, max_length=64)
    direction: str = "up"


class DeleteMeIn(BaseModel):
    """Opt-out exige confirmação explícita: a ação é irreversível."""
    password: str
    confirm: str = Field(description="precisa ser exatamente 'APAGAR'")
