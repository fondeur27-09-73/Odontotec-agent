import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from dotenv import load_dotenv

load_dotenv()

MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
BOT_OFF_LABEL = os.getenv("BOT_OFF_LABEL", "bot-off")

@asynccontextmanager
async def lifespan(app):
    from scheduler.reminders import start_scheduler
    scheduler = start_scheduler()
    yield
    scheduler.shutdown()

app = FastAPI(title="Odontotec Agent", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(request: Request):
    return {"status": "ok"}
