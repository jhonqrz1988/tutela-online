from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.api.tutelas import router as tutelas_router
from app.api.webhook_whatsapp import router as whatsapp_router
from app.bot.browser import BrowserManager
from app.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.tasks.scheduler import iniciar_scheduler
    iniciar_scheduler()
    yield
    await BrowserManager.close()


app = FastAPI(
    title="Tutelas Online AI",
    description="API para radicación automática de tutelas vía WhatsApp",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)
app.include_router(health_router)
app.include_router(whatsapp_router)
app.include_router(tutelas_router)
