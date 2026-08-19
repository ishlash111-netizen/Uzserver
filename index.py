"""UZUNITED Python compatibility backend.

Run locally:
    pip install fastapi uvicorn
    uvicorn python_backend:app --reload --port 8000

This file provides the public API layer expected by the supplied index.html.
The production admin panel and encrypted Telegram/S3/AI management remain in
 the main TypeScript backend project.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DB_PATH = Path(os.getenv("UZUNITED_DB_PATH", "uzunited.sqlite3"))
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "")

app = FastAPI(title="UZUNITED Public API", version="1.0.0")

if FRONTEND_ORIGIN:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    with closing(connect()) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                icon TEXT,
                published INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS banners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                link TEXT,
                image TEXT,
                image_alt TEXT,
                published INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS site_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                company_name TEXT NOT NULL DEFAULT 'UZUNITED',
                description TEXT,
                hero_url TEXT,
                hero_alt TEXT,
                instagram_url TEXT,
                telegram_url TEXT,
                facebook_url TEXT,
                linkedin_url TEXT,
                footer_text TEXT
            );
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                company TEXT,
                message TEXT,
                source TEXT,
                status TEXT NOT NULL DEFAULT 'new'
            );
            INSERT OR IGNORE INTO site_settings (id) VALUES (1);
            """
        )
        db.commit()


@app.on_event("startup")
def startup() -> None:
    initialize_database()


class LeadPayload(BaseModel):
    name: str = Field(min_length=1)
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    message: str | None = None
    source: str | None = "partner-form"


class ChatPayload(BaseModel):
    message: str = Field(min_length=1)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/services")
def get_services() -> dict[str, Any]:
    with closing(connect()) as db:
        rows = db.execute(
            "SELECT id, title, description, icon FROM services WHERE published = 1 ORDER BY id DESC"
        ).fetchall()
    return {"ok": True, "services": [dict(row) for row in rows]}


@app.get("/api/banners")
def get_banners() -> dict[str, Any]:
    with closing(connect()) as db:
        rows = db.execute(
            "SELECT id, tag, title, description, link, image, image_alt AS imageAlt "
            "FROM banners WHERE published = 1 ORDER BY id DESC"
        ).fetchall()
    return {"ok": True, "banners": [dict(row) for row in rows]}


@app.get("/api/site-settings")
def get_site_settings() -> dict[str, Any]:
    with closing(connect()) as db:
        row = db.execute(
            "SELECT company_name AS companyName, description, hero_url AS heroUrl, "
            "hero_alt AS heroAlt, instagram_url AS instagramUrl, telegram_url AS telegramUrl, "
            "facebook_url AS facebookUrl, linkedin_url AS linkedinUrl, footer_text AS footerText "
            "FROM site_settings WHERE id = 1"
        ).fetchone()
    return {"ok": True, "settings": dict(row) if row else None}


@app.post("/api/order")
def create_order(payload: LeadPayload) -> dict[str, Any]:
    with closing(connect()) as db:
        cursor = db.execute(
            "INSERT INTO leads (name, email, phone, company, message, source) VALUES (?, ?, ?, ?, ?, ?)",
            (payload.name, payload.email, payload.phone, payload.company, payload.message, payload.source),
        )
        db.commit()
    return {"ok": True, "leadId": cursor.lastrowid}


@app.post("/api/chat")
def chat(payload: ChatPayload) -> dict[str, str]:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Savol matnini kiriting.")
    return {
        "reply": "Savolingiz qabul qilindi. UZUNITED jamoasi tez orada siz bilan bog‘lanadi."
    }


@app.post("/api/leads")
def create_lead(payload: LeadPayload, request: Request) -> dict[str, Any]:
    return create_order(payload)
