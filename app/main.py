# Backend entry point, similar to app.js in Express.
# Read this file first, then api/elexon.py, schemas/elexon.py, and
# services/elexon_client.py to follow a request from URL to external data.
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.elexon import router as elexon_router
from app.api.forecast import router as forecast_router
from app.api.accuracy import router as accuracy_router

# Find backend/.env relative to this file, not the terminal's current folder.
# Missing .env files are allowed. Existing system environment values take priority.
load_dotenv(Path(__file__).resolve().parents[1] / '.env')
# __file__ is this module's file path. resolve() makes it absolute; parents[1]
# selects its grandparent folder (backend). With Path objects, / joins paths;
# here it does not divide numbers. Indexing starts at zero in Python and JS.
# FastAPI() creates the application, like express(). os.getenv reads a setting
# and uses the second argument if it is missing. Uvicorn runs this app; the
# command app.main:app means "load the app variable from app/main.py".
app = FastAPI(title=os.getenv('APP_NAME', 'WindSight API'), version='0.1.0')

# Browsers block a frontend from calling a backend on another web address unless
# the backend explicitly allows that frontend address. This rule is called CORS.
# FRONTEND_ORIGINS accepts a comma-separated list so local development, preview,
# and the deployed portal can all be allowed without changing Python code.
# rstrip('/') makes https://example.com and https://example.com/ equivalent here.
default_frontend_origins = (
    'http://localhost:5173,'
    'http://localhost:4173,'
    'http://localhost:8080'
)
frontend_origins = [
    origin.strip().rstrip('/')
    for origin in os.getenv('FRONTEND_ORIGINS', default_frontend_origins).split(',')
    if origin.strip()
]

# Middleware runs around every request. Only the listed websites receive
# permission to read this API from browser JavaScript. WindSight currently uses
# GET requests and no login cookies, so credentials remain disabled.
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=False,
    allow_methods=['GET'],
    allow_headers=['*'],
)

# include_router is like app.use('/api', router). These lines connect the
# route collections to the app. FastAPI also builds /docs from their definitions.
app.include_router(health_router, prefix='/api')
app.include_router(forecast_router, prefix='/api')
app.include_router(accuracy_router, prefix='/api')
app.include_router(elexon_router, prefix='/api')
