"""
ServiceOps – IT Service Management Backend (FastAPI + MongoDB).
"""
from dotenv import load_dotenv
load_dotenv()

import os
import uuid
import logging
import random
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import List, Optional, Literal, Dict, Any

from fastapi import (
    FastAPI, APIRouter, HTTPException, Depends, Request, Response,
    UploadFile, File, Query, Header
)
from fastapi.responses import StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr

from auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_admin, require_engineer, decode_token,
    extract_token,
)
from storage_client import init_storage, put_object, get_object, APP_NAME
from pdf_gen import build_service_report_pdf

# ---------- Setup ----------
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="ServiceOps API")
api = APIRouter(prefix="/api")

# ---------- Constants ----------
TICKET_STATUSES = ["open", "assigned", "accepted", "travelling",
                   "reached_site", "in_progress", "resolved", "completed",
                   "rejected"]

# ---------- Helpers ----------
def now_iso():
    return datetime.now(timezone.utc).isoformat()


def new_id():
    return str(uuid.uuid4())


def clean_doc(d: dict) -> dict:
    if d and "_id" in d:
        d.pop("_id", None)
    if d:
        d.pop("password_hash", None)
    return d


async def next_ticket_number() -> str:
    year = datetime.now(timezone.utc).year
    counter = await db.counters.find_one_and_update(
        {"_id": f"ticket_{year}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    seq = counter["seq"]
    return f"TKT-{year}-{seq:04d}"


async def next_device_id() -> str:
    year = datetime.now(timezone.utc).year
    counter = await db.counters.find_one_and_update(
        {"_id": f"device_{year}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    seq = counter["seq"]
    return f"DEV-{year}-{seq:04d}"


# ---------- Models ----------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class OTPVerifyRequest(BaseModel):
    email: EmailStr
    otp: str
    challenge_id: str


class UserOut(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: str
    phone: Optional[str] = None
    skills: Optional[List[str]] = None
    is_active: bool = True
    is_available: bool = True


class EngineerCreate(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    password: str
    skills: List[str] = []


class EngineerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    skills: Optional[List[str]] = None
    is_active: Optional[bool] = None
    is_available: Optional[bool] = None
    password: Optional[str] = None


class DeviceCreate(BaseModel):
    brand: str
    model: str
    serial_number: Optional[str] = None
    warranty_status: Literal["active", "expired", "none"] = "none"
    warranty_expiry: Optional[str] = None  # ISO date


class TicketCreate(BaseModel):
    customer_name: str
    customer_phone: str
    customer_company: Optional[str] = None
    contact_source: Literal["call", "whatsapp", "email"] = "call"
    problem_description: str
    device: DeviceCreate


class TicketAssign(BaseModel):
    engineer_id: str


class StatusUpdate(BaseModel):
    status: Literal["accepted", "travelling", "reached_site",
                    "in_progress", "resolved", "completed", "rejected"]
    note: Optional[str] = None
    reject_reason: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class LocationUpdate(BaseModel):
    latitude: float
    longitude: float


class PartItem(BaseModel):
    name: str
    part_number: Optional[str] = None
    quantity: int = 1


class ReportSubmit(BaseModel):
    work_notes: str
    parts_used: List[PartItem] = []
    photos_before: List[str] = []  # data URLs (base64)
    photos_after: List[str] = []
    customer_signature: str  # data URL of signature image
    customer_signed_name: Optional[str] = None


class AttendanceAction(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None


# ---------- Startup ----------
@app.on_event("startup")
async def startup():
    # Indexes
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
    await db.tickets.create_index("ticket_number", unique=True)
    await db.tickets.create_index("status")
    await db.tickets.create_index("assigned_engineer_id")
    await db.devices.create_index("device_id", unique=True)
    await db.devices.create_index("serial_number")
    await db.otp_challenges.create_index("expires_at", expireAfterSeconds=0)
    await db.files.create_index("storage_path")

    # Seed admin
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@serviceops.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "id": new_id(),
            "email": admin_email,
            "name": "Admin",
            "role": "admin",
            "password_hash": hash_password(admin_password),
            "is_active": True,
            "is_available": True,
            "created_at": now_iso(),
        })
        logger.info(f"Seeded admin: {admin_email}")
    else:
        # Keep password in sync with .env
        if not verify_password(admin_password, existing["password_hash"]):
            await db.users.update_one(
                {"email": admin_email},
                {"$set": {"password_hash": hash_password(admin_password)}}
            )

    # Seed a demo engineer
    eng_email = "engineer@serviceops.com"
    if not await db.users.find_one({"email": eng_email}):
        await db.users.insert_one({
            "id": new_id(),
            "email": eng_email,
            "name": "Rajiv Kumar",
            "role": "engineer",
            "phone": "+91 98765 43210",
            "password_hash": hash_password("engineer123"),
            "skills": ["Laptop Repair", "Networking", "Printer"],
            "is_active": True,
            "is_available": True,
            "created_at": now_iso(),
        })

    # Storage init
    init_storage()

    # Write test credentials file
    try:
        Path("/app/memory").mkdir(parents=True, exist_ok=True)
        Path("/app/memory/test_credentials.md").write_text(
            "# Test Credentials\n\n"
            "## Admin\n"
            f"- Email: `{admin_email}`\n"
            f"- Password: `{admin_password}`\n"
            "- Role: admin\n\n"
            "## Engineer\n"
            "- Email: `engineer@serviceops.com`\n"
            "- Password: `engineer123`\n"
            "- Role: engineer\n\n"
            "## Login flow\n"
            "1. POST /api/auth/login -> returns `challenge_id` and `dev_otp`\n"
            "2. POST /api/auth/verify-otp with `challenge_id` + `otp` -> returns JWT `token`\n"
            "3. Use `Authorization: Bearer <token>` for all subsequent calls\n"
        )
    except Exception as e:
        logger.error(f"Could not write test_credentials.md: {e}")


@app.on_event("shutdown")
async def shutdown():
    client.close()


# ---------- AUTH ----------
@api.post("/auth/login")
async def login(payload: LoginRequest):
    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account disabled")

    otp = f"{random.randint(0, 999999):06d}"
    challenge_id = new_id()
    await db.otp_challenges.insert_one({
        "id": challenge_id,
        "email": email,
        "otp": otp,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        "consumed": False,
    })
    logger.info(f"OTP for {email}: {otp}")
    # Mock OTP: return in response for demo
    return {
        "challenge_id": challenge_id,
        "dev_otp": otp,
        "message": "OTP sent. (Demo mode: OTP returned in response.)",
    }


@api.post("/auth/verify-otp")
async def verify_otp(payload: OTPVerifyRequest):
    email = payload.email.lower().strip()
    challenge = await db.otp_challenges.find_one({
        "id": payload.challenge_id,
        "email": email,
        "consumed": False,
    })
    if not challenge:
        raise HTTPException(status_code=400, detail="Invalid challenge")
    if challenge["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="OTP expired")
    if challenge["otp"] != payload.otp.strip():
        raise HTTPException(status_code=400, detail="Incorrect OTP")
    await db.otp_challenges.update_one(
        {"id": payload.challenge_id}, {"$set": {"consumed": True}}
    )
    user = await db.users.find_one({"email": email}, {"_id": 0, "password_hash": 0})
    token = create_access_token(user["id"], user["email"], user["role"])
    return {"token": token, "user": user}


@api.get("/auth/me")
async def me(user=Depends(get_current_user)):
    return user


@api.post("/auth/logout")
async def logout(user=Depends(get_current_user)):
    return {"ok": True}


# ---------- ENGINEERS ----------
@api.get("/engineers")
async def list_engineers(
    available_only: bool = False,
    user=Depends(get_current_user)
):
    q = {"role": "engineer"}
    if available_only:
        q["is_active"] = True
        q["is_available"] = True
    engs = await db.users.find(q, {"_id": 0, "password_hash": 0}).to_list(500)
    # Augment with live ticket counts
    for e in engs:
        e["active_tickets"] = await db.tickets.count_documents({
            "assigned_engineer_id": e["id"],
            "status": {"$nin": ["completed", "rejected"]},
        })
    return engs


@api.post("/engineers", dependencies=[Depends(require_admin)])
async def create_engineer(payload: EngineerCreate):
    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already exists")
    doc = {
        "id": new_id(),
        "email": email,
        "name": payload.name,
        "role": "engineer",
        "phone": payload.phone,
        "skills": payload.skills,
        "password_hash": hash_password(payload.password),
        "is_active": True,
        "is_available": True,
        "created_at": now_iso(),
    }
    await db.users.insert_one(doc)
    return clean_doc({**doc})


@api.patch("/engineers/{eng_id}", dependencies=[Depends(require_admin)])
async def update_engineer(eng_id: str, payload: EngineerUpdate):
    updates: Dict[str, Any] = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "password" in updates:
        updates["password_hash"] = hash_password(updates.pop("password"))
    if not updates:
        raise HTTPException(status_code=400, detail="No changes")
    res = await db.users.update_one({"id": eng_id, "role": "engineer"}, {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Engineer not found")
    eng = await db.users.find_one({"id": eng_id}, {"_id": 0, "password_hash": 0})
    return eng


@api.delete("/engineers/{eng_id}", dependencies=[Depends(require_admin)])
async def delete_engineer(eng_id: str):
    res = await db.users.delete_one({"id": eng_id, "role": "engineer"})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Engineer not found")
    return {"ok": True}


# ---------- DEVICES ----------
@api.get("/devices")
async def list_devices(q: Optional[str] = None, user=Depends(get_current_user)):
    query = {}
    if q:
        query["$or"] = [
            {"serial_number": {"$regex": q, "$options": "i"}},
            {"device_id": {"$regex": q, "$options": "i"}},
            {"brand": {"$regex": q, "$options": "i"}},
            {"model": {"$regex": q, "$options": "i"}},
        ]
    devices = await db.devices.find(query, {"_id": 0}).sort("created_at", -1).to_list(200)
    return devices


@api.get("/devices/{device_id}")
async def get_device(device_id: str, user=Depends(get_current_user)):
    device = await db.devices.find_one({"device_id": device_id}, {"_id": 0})
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    # All tickets for this device
    tickets = await db.tickets.find(
        {"device_id": device_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    # Augment with engineer name
    for t in tickets:
        if t.get("assigned_engineer_id"):
            eng = await db.users.find_one(
                {"id": t["assigned_engineer_id"]}, {"_id": 0, "name": 1}
            )
            t["engineer_name"] = eng["name"] if eng else None
    return {"device": device, "history": tickets}


# ---------- TICKETS ----------
async def _get_or_create_device(d: DeviceCreate) -> dict:
    """Return device doc, creating it if needed. Use serial number if provided."""
    serial = (d.serial_number or "").strip() or None
    existing = None
    if serial:
        existing = await db.devices.find_one({"serial_number": serial})
    if existing:
        # Update warranty if changed
        updates = {}
        if d.warranty_status != existing.get("warranty_status"):
            updates["warranty_status"] = d.warranty_status
        if d.warranty_expiry and d.warranty_expiry != existing.get("warranty_expiry"):
            updates["warranty_expiry"] = d.warranty_expiry
        if updates:
            await db.devices.update_one({"device_id": existing["device_id"]},
                                         {"$set": updates})
            existing.update(updates)
        return existing
    dev_id = await next_device_id()
    doc = {
        "device_id": dev_id,
        "serial_number": serial,
        "brand": d.brand,
        "model": d.model,
        "warranty_status": d.warranty_status,
        "warranty_expiry": d.warranty_expiry,
        "created_at": now_iso(),
    }
    await db.devices.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _log_activity(ticket_id: str, actor: dict, action: str,
                          details: Optional[str] = None):
    await db.activity.insert_one({
        "id": new_id(),
        "ticket_id": ticket_id,
        "actor_id": actor["id"],
        "actor_name": actor["name"],
        "actor_role": actor["role"],
        "action": action,
        "details": details,
        "timestamp": now_iso(),
    })


async def _ticket_with_details(ticket: dict) -> dict:
    if not ticket:
        return ticket
    ticket = clean_doc(ticket)
    device = await db.devices.find_one(
        {"device_id": ticket.get("device_id")}, {"_id": 0}
    )
    ticket["device"] = device
    if ticket.get("assigned_engineer_id"):
        eng = await db.users.find_one(
            {"id": ticket["assigned_engineer_id"]},
            {"_id": 0, "password_hash": 0}
        )
        ticket["engineer"] = eng
    return ticket


@api.post("/tickets", dependencies=[Depends(require_admin)])
async def create_ticket(payload: TicketCreate, admin=Depends(require_admin)):
    device = await _get_or_create_device(payload.device)
    ticket_number = await next_ticket_number()
    ticket = {
        "id": new_id(),
        "ticket_number": ticket_number,
        "customer_name": payload.customer_name,
        "customer_phone": payload.customer_phone,
        "customer_company": payload.customer_company,
        "contact_source": payload.contact_source,
        "problem_description": payload.problem_description,
        "device_id": device["device_id"],
        "status": "open",
        "assigned_engineer_id": None,
        "created_by": admin["id"],
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "engineer_location": None,
        "approved": False,
        "report_id": None,
        "pdf_path": None,
    }
    await db.tickets.insert_one(ticket)
    await _log_activity(ticket["id"], admin, "ticket_created",
                         f"Ticket {ticket_number} created")
    return await _ticket_with_details(ticket)


@api.get("/tickets")
async def list_tickets(
    status: Optional[str] = None,
    mine: bool = False,
    user=Depends(get_current_user)
):
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status
    if user["role"] == "engineer" or mine:
        q["assigned_engineer_id"] = user["id"]
    tickets = await db.tickets.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    # Augment
    for t in tickets:
        device = await db.devices.find_one(
            {"device_id": t.get("device_id")},
            {"_id": 0, "brand": 1, "model": 1, "device_id": 1, "warranty_status": 1}
        )
        t["device"] = device
        if t.get("assigned_engineer_id"):
            eng = await db.users.find_one(
                {"id": t["assigned_engineer_id"]},
                {"_id": 0, "name": 1, "id": 1}
            )
            t["engineer"] = eng
    return tickets


@api.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, user=Depends(get_current_user)):
    ticket = await db.tickets.find_one({"id": ticket_id}, {"_id": 0})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user["role"] == "engineer" and ticket.get("assigned_engineer_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your ticket")
    ticket = await _ticket_with_details(ticket)
    # Activity log
    activity = await db.activity.find(
        {"ticket_id": ticket_id}, {"_id": 0}
    ).sort("timestamp", -1).to_list(200)
    ticket["activity"] = activity
    # Report
    if ticket.get("report_id"):
        rep = await db.reports.find_one({"id": ticket["report_id"]}, {"_id": 0})
        ticket["report"] = rep
    # Device history (other tickets on same device)
    if ticket.get("device_id"):
        history = await db.tickets.find(
            {"device_id": ticket["device_id"], "id": {"$ne": ticket_id}},
            {"_id": 0, "ticket_number": 1, "status": 1, "created_at": 1,
             "problem_description": 1, "assigned_engineer_id": 1}
        ).sort("created_at", -1).to_list(20)
        ticket["device_history"] = history
    return ticket


@api.post("/tickets/{ticket_id}/assign", dependencies=[Depends(require_admin)])
async def assign_ticket(ticket_id: str, payload: TicketAssign,
                          admin=Depends(require_admin)):
    eng = await db.users.find_one(
        {"id": payload.engineer_id, "role": "engineer", "is_active": True}
    )
    if not eng:
        raise HTTPException(status_code=404, detail="Engineer not found")
    res = await db.tickets.update_one(
        {"id": ticket_id},
        {"$set": {
            "assigned_engineer_id": payload.engineer_id,
            "status": "assigned",
            "updated_at": now_iso(),
        }}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    await _log_activity(ticket_id, admin, "ticket_assigned",
                         f"Assigned to {eng['name']}")
    await db.notifications.insert_one({
        "id": new_id(),
        "user_id": payload.engineer_id,
        "ticket_id": ticket_id,
        "type": "ticket_assigned",
        "title": "New ticket assigned",
        "body": f"You have been assigned a new ticket",
        "read": False,
        "created_at": now_iso(),
    })
    return await _ticket_with_details(await db.tickets.find_one({"id": ticket_id}, {"_id": 0}))


@api.post("/tickets/{ticket_id}/status")
async def update_status(ticket_id: str, payload: StatusUpdate,
                          user=Depends(get_current_user)):
    ticket = await db.tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user["role"] == "engineer" and ticket.get("assigned_engineer_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your ticket")

    updates = {"status": payload.status, "updated_at": now_iso()}
    if payload.status == "rejected" and payload.reject_reason:
        updates["reject_reason"] = payload.reject_reason
        updates["assigned_engineer_id"] = None
        # send back to open pool
        updates["status"] = "open"
    if payload.latitude is not None and payload.longitude is not None:
        updates["engineer_location"] = {
            "lat": payload.latitude, "lng": payload.longitude,
            "updated_at": now_iso(),
        }
    await db.tickets.update_one({"id": ticket_id}, {"$set": updates})
    await _log_activity(
        ticket_id, user, f"status_{payload.status}",
        payload.note or payload.reject_reason
    )
    # notify admin on key status changes
    if payload.status in ("accepted", "rejected", "resolved", "completed"):
        await db.notifications.insert_one({
            "id": new_id(),
            "user_id": "admin",
            "ticket_id": ticket_id,
            "type": f"status_{payload.status}",
            "title": f"Ticket {ticket['ticket_number']} {payload.status}",
            "body": payload.note or payload.reject_reason or "",
            "read": False,
            "created_at": now_iso(),
        })
    return await _ticket_with_details(await db.tickets.find_one({"id": ticket_id}, {"_id": 0}))


@api.post("/tickets/{ticket_id}/location")
async def update_location(ticket_id: str, payload: LocationUpdate,
                            user=Depends(require_engineer)):
    ticket = await db.tickets.find_one({"id": ticket_id})
    if not ticket or ticket.get("assigned_engineer_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your ticket")
    await db.tickets.update_one(
        {"id": ticket_id},
        {"$set": {"engineer_location": {
            "lat": payload.latitude, "lng": payload.longitude,
            "updated_at": now_iso()
        }}}
    )
    return {"ok": True}


@api.post("/tickets/{ticket_id}/report")
async def submit_report(ticket_id: str, payload: ReportSubmit,
                          user=Depends(require_engineer)):
    ticket = await db.tickets.find_one({"id": ticket_id})
    if not ticket or ticket.get("assigned_engineer_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your ticket")

    device = await db.devices.find_one(
        {"device_id": ticket.get("device_id")}, {"_id": 0}
    )
    engineer = await db.users.find_one(
        {"id": user["id"]}, {"_id": 0, "password_hash": 0}
    )

    report_id = new_id()
    report = {
        "id": report_id,
        "ticket_id": ticket_id,
        "engineer_id": user["id"],
        "work_notes": payload.work_notes,
        "parts_used": [p.model_dump() for p in payload.parts_used],
        "photos_before": payload.photos_before,
        "photos_after": payload.photos_after,
        "customer_signature": payload.customer_signature,
        "customer_signed_name": payload.customer_signed_name,
        "signed_at": now_iso(),
        "created_at": now_iso(),
    }

    # Generate PDF
    try:
        pdf_bytes = build_service_report_pdf(
            ticket=ticket, device=device, engineer=engineer, report=report
        )
        path = f"{APP_NAME}/reports/{ticket['ticket_number']}-{report_id}.pdf"
        put_object(path, pdf_bytes, "application/pdf")
        await db.files.insert_one({
            "id": new_id(),
            "storage_path": path,
            "ticket_id": ticket_id,
            "content_type": "application/pdf",
            "size": len(pdf_bytes),
            "is_deleted": False,
            "created_at": now_iso(),
        })
        report["pdf_path"] = path
    except Exception as e:
        logger.error(f"PDF generation/upload failed: {e}")
        report["pdf_path"] = None

    await db.reports.insert_one(report)
    await db.tickets.update_one(
        {"id": ticket_id},
        {"$set": {
            "report_id": report_id,
            "status": "resolved",
            "pdf_path": report.get("pdf_path"),
            "updated_at": now_iso(),
        }}
    )
    await _log_activity(ticket_id, user, "report_submitted",
                         "Service report submitted with signature")
    await db.notifications.insert_one({
        "id": new_id(),
        "user_id": "admin",
        "ticket_id": ticket_id,
        "type": "report_ready",
        "title": f"Service report ready: {ticket['ticket_number']}",
        "body": "Customer-signed PDF is ready for review.",
        "read": False,
        "created_at": now_iso(),
    })
    report.pop("_id", None)
    return report


@api.post("/tickets/{ticket_id}/approve", dependencies=[Depends(require_admin)])
async def approve_ticket(ticket_id: str, admin=Depends(require_admin)):
    ticket = await db.tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    await db.tickets.update_one(
        {"id": ticket_id},
        {"$set": {
            "approved": True,
            "status": "completed",
            "updated_at": now_iso(),
            "closed_at": now_iso(),
        }}
    )
    await _log_activity(ticket_id, admin, "ticket_approved",
                         "Service report approved and ticket closed")
    return await _ticket_with_details(await db.tickets.find_one({"id": ticket_id}, {"_id": 0}))


@api.get("/tickets/{ticket_id}/pdf")
async def get_ticket_pdf(ticket_id: str, request: Request):
    # Auth via header or ?auth=token (for direct PDF download links)
    token = extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token)
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    ticket = await db.tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user["role"] == "engineer" and ticket.get("assigned_engineer_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your ticket")
    if not ticket.get("pdf_path"):
        raise HTTPException(status_code=404, detail="PDF not generated yet")
    try:
        data, ctype = get_object(ticket["pdf_path"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF fetch failed: {e}")
    headers = {
        "Content-Disposition": f"inline; filename=\"{ticket['ticket_number']}.pdf\""
    }
    return Response(content=data, media_type="application/pdf", headers=headers)


# ---------- NOTIFICATIONS ----------
@api.get("/notifications")
async def list_notifications(user=Depends(get_current_user)):
    target = "admin" if user["role"] == "admin" else user["id"]
    notes = await db.notifications.find(
        {"user_id": target}, {"_id": 0}
    ).sort("created_at", -1).limit(50).to_list(50)
    return notes


@api.post("/notifications/{nid}/read")
async def mark_read(nid: str, user=Depends(get_current_user)):
    await db.notifications.update_one({"id": nid}, {"$set": {"read": True}})
    return {"ok": True}


# ---------- ATTENDANCE ----------
@api.post("/attendance/check-in")
async def check_in(payload: AttendanceAction, user=Depends(require_engineer)):
    today = date.today().isoformat()
    existing = await db.attendance.find_one({"engineer_id": user["id"], "date": today})
    if existing and existing.get("check_in"):
        raise HTTPException(status_code=400, detail="Already checked in today")
    doc = {
        "id": new_id(),
        "engineer_id": user["id"],
        "date": today,
        "check_in": now_iso(),
        "check_in_location": {"lat": payload.latitude, "lng": payload.longitude}
            if payload.latitude is not None else None,
        "check_out": None,
    }
    if existing:
        await db.attendance.update_one({"id": existing["id"]}, {"$set": doc})
    else:
        await db.attendance.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.post("/attendance/check-out")
async def check_out(payload: AttendanceAction, user=Depends(require_engineer)):
    today = date.today().isoformat()
    existing = await db.attendance.find_one({"engineer_id": user["id"], "date": today})
    if not existing or not existing.get("check_in"):
        raise HTTPException(status_code=400, detail="Not checked in")
    if existing.get("check_out"):
        raise HTTPException(status_code=400, detail="Already checked out")
    await db.attendance.update_one(
        {"id": existing["id"]},
        {"$set": {
            "check_out": now_iso(),
            "check_out_location": {"lat": payload.latitude, "lng": payload.longitude}
                if payload.latitude is not None else None,
        }}
    )
    doc = await db.attendance.find_one({"id": existing["id"]}, {"_id": 0})
    return doc


@api.get("/attendance/today")
async def attendance_today(user=Depends(require_engineer)):
    today = date.today().isoformat()
    doc = await db.attendance.find_one({"engineer_id": user["id"], "date": today},
                                         {"_id": 0})
    return doc or {}


@api.get("/attendance/history")
async def attendance_history(user=Depends(require_engineer)):
    docs = await db.attendance.find(
        {"engineer_id": user["id"]}, {"_id": 0}
    ).sort("date", -1).limit(60).to_list(60)
    return docs


# ---------- DASHBOARD ----------
@api.get("/dashboard/admin", dependencies=[Depends(require_admin)])
async def admin_dashboard():
    counts = {}
    for s in TICKET_STATUSES:
        counts[s] = await db.tickets.count_documents({"status": s})
    counts["total"] = sum(counts.values())
    counts["active"] = sum(
        counts[s] for s in
        ["open", "assigned", "accepted", "travelling",
         "reached_site", "in_progress", "resolved"]
    )
    total_eng = await db.users.count_documents({"role": "engineer", "is_active": True})
    available = await db.users.count_documents(
        {"role": "engineer", "is_active": True, "is_available": True}
    )
    recent = await db.activity.find({}, {"_id": 0}).sort("timestamp", -1).limit(15).to_list(15)
    # Warranty alerts: expiring in next 30 days
    today = date.today()
    in_30 = (today + timedelta(days=30)).isoformat()
    warranty_alerts = await db.devices.find(
        {"warranty_status": "active",
         "warranty_expiry": {"$gte": today.isoformat(), "$lte": in_30}},
        {"_id": 0}
    ).to_list(20)
    return {
        "ticket_counts": counts,
        "engineers": {"total": total_eng, "available": available},
        "recent_activity": recent,
        "warranty_alerts": warranty_alerts,
    }


@api.get("/dashboard/engineer")
async def engineer_dashboard(user=Depends(require_engineer)):
    eid = user["id"]
    assigned = await db.tickets.count_documents(
        {"assigned_engineer_id": eid, "status": {"$in": ["assigned", "accepted"]}}
    )
    in_progress = await db.tickets.count_documents(
        {"assigned_engineer_id": eid,
         "status": {"$in": ["travelling", "reached_site", "in_progress"]}}
    )
    completed = await db.tickets.count_documents(
        {"assigned_engineer_id": eid, "status": "completed"}
    )
    resolved = await db.tickets.count_documents(
        {"assigned_engineer_id": eid, "status": "resolved"}
    )
    return {
        "assigned": assigned,
        "in_progress": in_progress,
        "resolved": resolved,
        "completed": completed,
    }


# ---------- ANALYTICS ----------
@api.get("/analytics", dependencies=[Depends(require_admin)])
async def analytics():
    # Tickets per day (last 14 days)
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    per_day = []
    for d in days:
        c = await db.tickets.count_documents({
            "created_at": {"$gte": d, "$lt": d + "T99"}
        })
        per_day.append({"date": d, "count": c})
    # Engineer performance
    engineers = await db.users.find(
        {"role": "engineer"}, {"_id": 0, "id": 1, "name": 1}
    ).to_list(200)
    perf = []
    for e in engineers:
        completed = await db.tickets.count_documents(
            {"assigned_engineer_id": e["id"], "status": "completed"}
        )
        active = await db.tickets.count_documents(
            {"assigned_engineer_id": e["id"],
             "status": {"$nin": ["completed", "rejected"]}}
        )
        perf.append({"name": e["name"], "completed": completed, "active": active})
    perf.sort(key=lambda x: -x["completed"])
    # Repeat complaints (devices with > 1 ticket)
    pipeline = [
        {"$group": {"_id": "$device_id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    repeats = []
    async for row in db.tickets.aggregate(pipeline):
        device = await db.devices.find_one({"device_id": row["_id"]}, {"_id": 0})
        if device:
            repeats.append({
                "device_id": row["_id"],
                "brand": device.get("brand"),
                "model": device.get("model"),
                "count": row["count"],
            })
    # Device failure trends by brand
    pipeline2 = [
        {"$group": {"_id": "$device_id", "count": {"$sum": 1}}},
    ]
    brand_counts: Dict[str, int] = {}
    async for row in db.tickets.aggregate(pipeline2):
        device = await db.devices.find_one({"device_id": row["_id"]}, {"_id": 0})
        if device:
            b = device.get("brand", "Unknown")
            brand_counts[b] = brand_counts.get(b, 0) + row["count"]
    brand_trend = [{"brand": k, "tickets": v} for k, v in
                   sorted(brand_counts.items(), key=lambda x: -x[1])][:8]
    # Warranty expiring soon
    in_30 = (today + timedelta(days=30)).isoformat()
    warranty_alerts = await db.devices.find(
        {"warranty_status": "active",
         "warranty_expiry": {"$gte": today.isoformat(), "$lte": in_30}},
        {"_id": 0}
    ).to_list(20)
    return {
        "per_day": per_day,
        "engineer_performance": perf,
        "repeat_complaints": repeats,
        "brand_trend": brand_trend,
        "warranty_alerts": warranty_alerts,
    }


# ---------- Live engineer locations (admin) ----------
@api.get("/live-locations", dependencies=[Depends(require_admin)])
async def live_locations():
    tickets = await db.tickets.find(
        {"engineer_location": {"$ne": None},
         "status": {"$in": ["travelling", "reached_site", "in_progress"]}},
        {"_id": 0}
    ).to_list(200)
    out = []
    for t in tickets:
        eng = await db.users.find_one(
            {"id": t.get("assigned_engineer_id")}, {"_id": 0, "name": 1}
        ) if t.get("assigned_engineer_id") else None
        out.append({
            "ticket_id": t["id"],
            "ticket_number": t["ticket_number"],
            "engineer_name": eng["name"] if eng else "Unknown",
            "status": t["status"],
            "location": t["engineer_location"],
            "customer_name": t.get("customer_name"),
        })
    return out


app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
