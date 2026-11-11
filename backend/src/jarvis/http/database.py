from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session


def db_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.database(), expire_on_commit=False) as db:
        yield db


Db = Annotated[Session, Depends(db_session)]
