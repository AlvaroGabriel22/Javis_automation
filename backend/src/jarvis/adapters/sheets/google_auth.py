"""Credencial dedicada de uma conta de serviço, com escopo exclusivo do Sheets."""

import base64
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

import requests
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.adapters.sheets.layout import SheetError
from jarvis.bootstrap.settings import ROOT

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class GoogleSheetSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    jarvis_google_sheets_id: str = ""
    jarvis_google_sheets_credentials: str = ""

    @property
    def signature(self) -> str | None:
        if self.jarvis_google_sheets_credentials.strip() and re.fullmatch(
            r"[A-Za-z0-9_-]{10,160}", self.jarvis_google_sheets_id
        ):
            return hashlib.sha256(self.jarvis_google_sheets_id.encode()).hexdigest()
        return None


def settings() -> GoogleSheetSettings:
    return GoogleSheetSettings(_env_file=os.getenv("JARVIS_ENV_FILE", str(ROOT / ".env")))  # pyright: ignore[reportCallIssue]


def google_destination_id() -> str | None:
    return settings().signature


def google_configured() -> bool:
    return google_destination_id() is not None


def _part(data: dict) -> bytes:
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=")


def access_token(session: requests.Session, credential: Path) -> str:
    if not credential.is_absolute():
        raise SheetError("Use um caminho absoluto para a credencial do Google Sheets.")
    try:
        with os.fdopen(
            os.open(credential, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
        ) as file:
            info = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or not 0 < info.st_size <= 65536
            ):
                raise SheetError(
                    "Guarde a chave do Sheets num arquivo privado do usuário (chmod 600)."
                )
            identity = json.load(file)
        if (
            identity["type"] != "service_account"
            or identity.get("token_uri") != TOKEN_URL
            or not re.fullmatch(r"[\w.+-]+@[\w.-]+\.gserviceaccount\.com", identity["client_email"])
        ):
            raise ValueError
        key = serialization.load_pem_private_key(identity["private_key"].encode(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError
        now = int(time.time())
        unsigned = (
            _part({"alg": "RS256", "typ": "JWT"})
            + b"."
            + _part(
                {
                    "iss": identity["client_email"],
                    "scope": SCOPE,
                    "aud": TOKEN_URL,
                    "iat": now,
                    "exp": now + 600,
                }
            )
        )
        signature = key.sign(unsigned, padding.PKCS1v15(), hashes.SHA256())
        assertion = (unsigned + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()
    except (
        OSError,
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
        UnsupportedAlgorithm,
    ) as error:
        raise SheetError("Confira o JSON local da conta de serviço do Google Sheets.") from error
    try:
        http = session.post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=(5, 15),
            allow_redirects=False,
        )
        if http.status_code != 200:
            raise SheetError(
                "O Google recusou a credencial da planilha. Revise a conta de serviço."
            )
        token = http.json()["access_token"]
        if not isinstance(token, str) or not re.fullmatch(r"[\w./=+-]{10,4096}", token):
            raise ValueError
        return token
    except (requests.RequestException, KeyError, ValueError, TypeError) as error:
        raise SheetError("O Google não confirmou a autorização da planilha.") from error
