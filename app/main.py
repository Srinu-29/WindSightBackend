import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.elexon import router as elexon_router

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
app = FastAPI(title=os.getenv('APP_NAME', 'WindSight API'), version='0.1.0')
app.include_router(health_router, prefix='/api')
if os.getenv('ENABLE_ELEXON_INSPECTION', 'true').lower() == 'true':
    app.include_router(elexon_router, prefix='/api')
