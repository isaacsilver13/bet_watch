"""Version 1 of the daily NBA forecast producer/consumer contract."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_date: date
    home: str = Field(pattern=r"^[A-Z]{2,4}$")
    away: str = Field(pattern=r"^[A-Z]{2,4}$")
    pred_margin: float = Field(allow_inf_nan=False)
    p_home_win: float = Field(ge=0, le=1, allow_inf_nan=False)
    sigma: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def different_teams(self):
        if self.home == self.away:
            raise ValueError("home and away teams must differ")
        return self


class Predictions(BaseModel):
    """The existing predict_today artifact, with an optional explicit schema marker."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    slate_date: date
    model_version: str = Field(min_length=1, max_length=80)
    generated_at: datetime
    data_through: date
    games: list[Prediction] = Field(min_length=1, max_length=20)

    @field_validator("generated_at")
    @classmethod
    def aware_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a time zone")
        return value

    @model_validator(mode="after")
    def consistent_slate(self):
        if self.data_through >= self.slate_date:
            raise ValueError("data_through must precede slate_date")
        keys = [(game.game_date, game.home, game.away) for game in self.games]
        if any(game.game_date != self.slate_date for game in self.games):
            raise ValueError("each game_date must match slate_date")
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate games in forecast")
        return self
