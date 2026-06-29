"""FastAPI app exposing the replay parser as a single endpoint.

Designed to be called from the Nexus League admin tRPC layer:
    1. Admin uploads a .rofl in the back-office form.
    2. Next.js streams the file as multipart/form-data to `POST /replays`.
    3. We return the enriched ParsedReplay JSON.
    4. The admin UI displays 10 champion rows (ordered, role pre-assigned by
       position) with a Player selector per row, filtered by `role`.
    5. On submit, Next.js writes `PlayerMatchStats` rows using the `prisma`
       sub-object + the selected `playerId/matchGameId/teamId`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import tempfile
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .models import ParsedReplay
from .parser import ReplayParseError, parse_rofl
from .stats import enrich

# .rofl files can run 20–40 MB. Cap at 100 MB to keep memory bounded.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

app = FastAPI(
    title="Nexus League — Replay Stats",
    version=__version__,
    description="Extract scoreboard stats from .rofl replay files.",
)

# Le navigateur uploade les .rofl directement ici (cross-origin depuis l'app
# web), pour contourner la limite de 4,5 Mo des fonctions serverless Vercel.
# ALLOWED_ORIGINS = liste separee par des virgules, ou "*" (defaut).
_origins_env = os.environ.get("ALLOWED_ORIGINS", "*").strip()
_allow_origins = (
    ["*"]
    if _origins_env == "*"
    else [o.strip() for o in _origins_env.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)


def _sign_ticket(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def require_ticket(authorization: str | None = Header(default=None)) -> None:
    """Valide le ticket d'upload signe (HMAC-SHA256) emis par l'app web.

    Format attendu : `Bearer <exp>.<base64url(hmac)>` ou exp est un timestamp
    UNIX en secondes. Si REPLAY_UPLOAD_SECRET n'est pas defini (dev local),
    l'authentification est desactivee.
    """
    secret = os.environ.get("REPLAY_UPLOAD_SECRET")
    if not secret:
        return

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing upload ticket")

    token = authorization[len("Bearer ") :]
    dot = token.find(".")
    if dot <= 0:
        raise HTTPException(status_code=401, detail="malformed upload ticket")

    exp_part, sig_part = token[:dot], token[dot + 1 :]
    try:
        exp = int(exp_part)
    except ValueError as e:
        raise HTTPException(status_code=401, detail="malformed upload ticket") from e

    if exp < int(time.time()):
        raise HTTPException(status_code=401, detail="upload ticket expired")

    expected = _sign_ticket(secret, exp_part)
    if not hmac.compare_digest(sig_part, expected):
        raise HTTPException(status_code=401, detail="invalid upload ticket")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/replays", response_model=ParsedReplay, dependencies=[Depends(require_ticket)])
async def parse_replay(file: UploadFile = File(...)) -> ParsedReplay:
    if not file.filename or not file.filename.lower().endswith(".rofl"):
        raise HTTPException(status_code=400, detail="expected a .rofl file")

    # Stream to a temp file so the binary parser can seek freely (it needs
    # SEEK_END for ROFL2). UploadFile.read() into memory works too but for
    # 40 MB files a temp file is friendlier.
    with tempfile.NamedTemporaryFile(suffix=".rofl", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        total = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                tmp.close()
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
                )
            tmp.write(chunk)

    try:
        try:
            raw_meta = parse_rofl(tmp_path)
        except ReplayParseError as e:
            raise HTTPException(status_code=422, detail=f"invalid .rofl: {e}") from e

        try:
            return enrich(raw_meta)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        tmp_path.unlink(missing_ok=True)
