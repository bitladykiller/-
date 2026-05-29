from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.api import api_router


app = FastAPI(title="AssistGen REST API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


STATIC_DIR = Path(__file__).parent / "static" / "dist"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
