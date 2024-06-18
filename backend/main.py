"""
Running in development environment: `fastapi dev ./backend`
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import course_router, manage_router


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(course_router)
app.include_router(manage_router)
