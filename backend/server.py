"""
Plutus Ventures – IT Service Management Backend
FastAPI + MongoDB (Motor async). JWT + OTP auth.
Local filesystem storage for PDFs/photos. PDF reports include QR code.
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
    Query
)
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr

from auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_admin, require_engineer, decode_token,
)
from storage_client import init_storage, put_object, get_object
from pdf_gen import build_service_report_pdf

# ---------- Setup ----------
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="Plutus Ventures – IT Service Management API",
              version="2.0.0")
api = APIRouter(prefix="/api")

# ---------- Constants ----------
# Full workflow per new requirements
TICKET_STATUSES = [
    "open", "assigned", "accepted", "travelling", "reached_site",
    "in_progress", "resolved",
    "completed_with_signature", "report_generated", "closed",
    "rejected",
]


# ---------- Helpers ----------
def now_iso():
    return datetime.now(timezone.utc).isoformat()


def new_id():
    return str(uuid.uuid4())


def clean(d):
    if not d:
        return d
    d.pop("_id", None)
    d.pop("password_hash", None)
    return d


async def _seq(name: str) -> int:
    doc = await db.counters.find_one_and_update(
        {"_id": name}, {"$inc": {"sequence_value": 1}},
        upsert=True, return_document=True,
    )
    return doc["sequence_value"]


async def next_ticket_number() -> str:
    year = datetime.now(timezone.utc).year
    seq = await _seq(f"ticket_{year}")
    return f"TKT-{year}-{seq:04d}"


async def next_device_id() -> str:
    year = datetime.now(timezone.utc).year
    seq = await _seq(f"device_{year}")
    return f"DEV-{year}-{seq:04d}"


async def next_company_code() -> str:
    seq = await _seq("company")
    return f"CMP-{seq:04d}"


# ---------- Models ----------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class OTPVerifyRequest(BaseModel):
    email: EmailStr
    otp: str
    challenge_id: str


class EngineerCreate(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    password: str
    skills: List[str] = []
    employee_id: Optional[str] = None
    designation: Optional[str] = None
    address: Optional[str] = None


class EngineerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    skills: Optional[List[str]] = None
    is_active: Optional[bool] = None
    is_available: Optional[bool] = None
    password: Optional[str] = None
    employee_id: Optional[str] = None
    designation: Optional[str] = None
    address: Optional[str] = None


class CompanyCreate(BaseModel):
    company_name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    address: Optional[str] = None
    gst_number: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None


class CompanyUpdate(BaseModel):
    company_name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    address: Optional[str] = None
    gst_number: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    status: Optional[Literal["active", "inactive"]] = None


class DeviceCreate(BaseModel):
    brand: str
    model: str
    serial_number: Optional[str] = None
    device_name: Optional[str] = None
    device_type: Optional[str] = None
    warranty_status: Literal["active", "expired", "none"] = "none"
    warranty_expiry: Optional[str] = None
    purchase_date: Optional[str] = None
    notes: Optional[str] = None


class TicketCreate(BaseModel):
    company_id: str
    customer_name: Optional[str] = None  # falls back to company contact_person
    customer_phone: Optional[str] = None
    contact_source: Literal["call", "whatsapp", "email"] = "call"
    issue_description: str
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    device: DeviceCreate


class TicketAssign(BaseModel):
    engineer_id: str


class StatusUpdate(BaseModel):
    status: Literal[
        "accepted", "travelling", "reached_site", "in_progress",
        "resolved", "completed_with_signature", "report_generated",
        "closed", "rejected",
    ]
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
    engineer_notes: str
    resolution_summary: Optional[str] = None
    parts_used: List[PartItem] = []
    before_images: List[str] = []   # base64 data URLs
    after_images: List[str] = []
    customer_signature: str
    customer_signed_name: Optional[str] = None


class AttendanceAction(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None


# ---------- Startup ----------
@app.on_event("startup")
async def startup():
    # ---- Migrations: backfill new fields on existing docs ----
    await db.tickets.update_many(
        {"ticket_no": {"$exists": False}, "ticket_number": {"$exists": True}},
        [{"$set": {"ticket_no": "$ticket_number"}}],
    )
    await db.tickets.update_many(
        {"issue_description": {"$exists": False}, "problem_description": {"$exists": True}},
        [{"$set": {"issue_description": "$problem_description"}}],
    )
    # Drop legacy indexes that conflict
    try:
        await db.tickets.drop_index("ticket_number_1")
    except Exception:
        pass
    try:
        await db.devices.drop_index("serial_number_1")
    except Exception:
        pass

    # Sync counters with existing data (in case devices/tickets exist from prior runs)
    year = datetime.now(timezone.utc).year
    last_device = await db.devices.find_one(
        {"device_id": {"$regex": f"^DEV-{year}-"}}, sort=[("device_id", -1)]
    )
    if last_device:
        try:
            seq = int(last_device["device_id"].split("-")[-1])
            await db.counters.update_one(
                {"_id": f"device_{year}"},
                {"$max": {"sequence_value": seq}},
                upsert=True,
            )
        except Exception:
            pass
    last_ticket = await db.tickets.find_one(
        {"ticket_no": {"$regex": f"^TKT-{year}-"}}, sort=[("ticket_no", -1)]
    )
    if last_ticket:
        try:
            seq = int(last_ticket["ticket_no"].split("-")[-1])
            await db.counters.update_one(
                {"_id": f"ticket_{year}"},
                {"$max": {"sequence_value": seq}},
                upsert=True,
            )
        except Exception:
            pass

    # Indexes
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
    await db.users.create_index("employee_id", sparse=True)
    await db.companies.create_index("company_name", unique=True)
    await db.companies.create_index("company_code", unique=True)
    await db.companies.create_index("status")
    await db.tickets.create_index("ticket_no", unique=True, sparse=True)
    await db.tickets.create_index("status")
    await db.tickets.create_index("assigned_engineer_id")
    await db.tickets.create_index("company_id")
    await db.devices.create_index("device_id", unique=True)
    await db.devices.create_index("serial_number", sparse=True)
    await db.devices.create_index("company_id")
    await db.ticket_status_logs.create_index("ticket_id")
    await db.ticket_status_logs.create_index("timestamp")
    await db.service_reports.create_index("ticket_id", unique=True)
    await db.attachments.create_index("ticket_id")
    await db.notifications.create_index("user_id")
    await db.otp_challenges.create_index("expires_at", expireAfterSeconds=0)

    # Seed admin
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@plutusventures.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "id": new_id(),
            "email": admin_email,
            "name": "Administrator",
            "role": "admin",
            "password_hash": hash_password(admin_password),
            "is_active": True,
            "is_available": True,
            "status": "active",
            "designation": "System Admin",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })
        logger.info(f"Seeded admin: {admin_email}")
    else:
        if not verify_password(admin_password, existing["password_hash"]):
            await db.users.update_one(
                {"email": admin_email},
                {"$set": {"password_hash": hash_password(admin_password),
                          "updated_at": now_iso()}}
            )
    # Also keep legacy admin@serviceops.com if it exists (don't break older flow)
    legacy = await db.users.find_one({"email": "admin@serviceops.com"})
    if not legacy:
        await db.users.insert_one({
            "id": new_id(),
            "email": "admin@serviceops.com",
            "name": "Admin (legacy)",
            "role": "admin",
            "password_hash": hash_password("admin123"),
            "is_active": True,
            "is_available": True,
            "status": "active",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })

    # Seed engineer
    eng_email = "engineer@plutusventures.com"
    if not await db.users.find_one({"email": eng_email}):
        await db.users.insert_one({
            "id": new_id(),
            "email": eng_email,
            "name": "Rajiv Kumar",
            "role": "engineer",
            "phone": "+91 98765 43210",
            "password_hash": hash_password("engineer123"),
            "skills": ["Laptop Repair", "Networking", "Printer"],
            "employee_id": "EMP-001",
            "designation": "Senior Field Engineer",
            "is_active": True,
            "is_available": True,
            "status": "active",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })
    # Legacy engineer
    legacy_eng = await db.users.find_one({"email": "engineer@serviceops.com"})
    if not legacy_eng:
        await db.users.insert_one({
            "id": new_id(),
            "email": "engineer@serviceops.com",
            "name": "Field Engineer (legacy)",
            "role": "engineer",
            "phone": "+91 90000 00000",
            "password_hash": hash_password("engineer123"),
            "skills": ["Laptop Repair"],
            "employee_id": "EMP-LEG",
            "is_active": True,
            "is_available": True,
            "status": "active",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })

    # Init local filesystem storage
    init_storage()

    # Write test credentials
    try:
        Path("/app/memory").mkdir(parents=True, exist_ok=True)
        Path("/app/memory/test_credentials.md").write_text(
            "# Test Credentials\n\n"
            "## Admin (primary)\n"
            f"- Email: `{admin_email}`\n"
            f"- Password: `{admin_password}`\n\n"
            "## Admin (legacy)\n"
            "- Email: `admin@serviceops.com` / Password: `admin123`\n\n"
            "## Engineer (primary)\n"
            "- Email: `engineer@plutusventures.com` / Password: `engineer123`\n\n"
            "## Engineer (legacy)\n"
            "- Email: `engineer@serviceops.com` / Password: `engineer123`\n\n"
            "## Login flow\n"
            "1. POST /api/auth/login -> returns `challenge_id` and `dev_otp`\n"
            "2. POST /api/auth/verify-otp -> returns JWT `token`\n"
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
    return {
        "challenge_id": challenge_id,
        "dev_otp": otp,
        "message": "OTP sent. (Demo mode: OTP returned in response.)",
    }


@api.post("/auth/verify-otp")
async def verify_otp(payload: OTPVerifyRequest):
    email = payload.email.lower().strip()
    challenge = await db.otp_challenges.find_one({
        "id": payload.challenge_id, "email": email, "consumed": False,
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
    await db.users.update_one({"email": email}, {"$set": {"last_login": now_iso()}})
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
async def list_engineers(available_only: bool = False,
                         user=Depends(get_current_user)):
    q = {"role": "engineer"}
    if available_only:
        q["is_active"] = True
        q["is_available"] = True
    engs = await db.users.find(q, {"_id": 0, "password_hash": 0}).to_list(500)
    for e in engs:
        e["active_tickets"] = await db.tickets.count_documents({
            "assigned_engineer_id": e["id"],
            "status": {"$nin": ["closed", "rejected", "report_generated"]},
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
        "employee_id": payload.employee_id,
        "designation": payload.designation,
        "address": payload.address,
        "password_hash": hash_password(payload.password),
        "is_active": True,
        "is_available": True,
        "status": "active",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.users.insert_one(doc)
    return clean({**doc})


@api.patch("/engineers/{eng_id}", dependencies=[Depends(require_admin)])
async def update_engineer(eng_id: str, payload: EngineerUpdate):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "password" in updates:
        updates["password_hash"] = hash_password(updates.pop("password"))
    updates["updated_at"] = now_iso()
    if not updates:
        raise HTTPException(status_code=400, detail="No changes")
    res = await db.users.update_one({"id": eng_id, "role": "engineer"},
                                      {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Engineer not found")
    return await db.users.find_one({"id": eng_id}, {"_id": 0, "password_hash": 0})


@api.delete("/engineers/{eng_id}", dependencies=[Depends(require_admin)])
async def delete_engineer(eng_id: str):
    res = await db.users.delete_one({"id": eng_id, "role": "engineer"})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Engineer not found")
    return {"ok": True}


# ---------- COMPANIES ----------
@api.get("/companies")
async def list_companies(
    q: Optional[str] = None,
    status: Optional[Literal["active", "inactive"]] = None,
    page: int = 1, page_size: int = 50,
    user=Depends(get_current_user),
):
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if q:
        query["$or"] = [
            {"company_name": {"$regex": q, "$options": "i"}},
            {"company_code": {"$regex": q, "$options": "i"}},
            {"contact_person": {"$regex": q, "$options": "i"}},
            {"gst_number": {"$regex": q, "$options": "i"}},
            {"city": {"$regex": q, "$options": "i"}},
        ]
    total = await db.companies.count_documents(query)
    skip = max(0, (page - 1)) * page_size
    items = await db.companies.find(query, {"_id": 0}) \
        .sort("created_at", -1).skip(skip).limit(page_size).to_list(page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@api.get("/companies/{company_id}")
async def get_company(company_id: str, user=Depends(get_current_user)):
    c = await db.companies.find_one({"id": company_id}, {"_id": 0})
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")
    # Recent tickets for this company
    tickets = await db.tickets.find(
        {"company_id": company_id}, {"_id": 0}
    ).sort("created_at", -1).limit(20).to_list(20)
    devices = await db.devices.find(
        {"company_id": company_id}, {"_id": 0}
    ).sort("created_at", -1).limit(50).to_list(50)
    return {"company": c, "tickets": tickets, "devices": devices}


@api.post("/companies", dependencies=[Depends(require_admin)])
async def create_company(payload: CompanyCreate, admin=Depends(require_admin)):
    name = payload.company_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Company name required")
    if await db.companies.find_one({"company_name": {"$regex": f"^{name}$", "$options": "i"}}):
        raise HTTPException(status_code=400, detail="Company name already exists")
    doc = {
        "id": new_id(),
        "company_name": name,
        "company_code": await next_company_code(),
        "contact_person": payload.contact_person,
        "phone": payload.phone,
        "email": payload.email,
        "address": payload.address,
        "gst_number": payload.gst_number,
        "city": payload.city,
        "state": payload.state,
        "pincode": payload.pincode,
        "status": "active",
        "created_by": admin["id"],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.companies.insert_one(doc)
    return clean({**doc})


@api.put("/companies/{company_id}", dependencies=[Depends(require_admin)])
async def update_company(company_id: str, payload: CompanyUpdate):
    existing = await db.companies.find_one({"id": company_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Company not found")
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "company_name" in updates:
        new_name = updates["company_name"].strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="Company name required")
        dup = await db.companies.find_one({
            "company_name": {"$regex": f"^{new_name}$", "$options": "i"},
            "id": {"$ne": company_id},
        })
        if dup:
            raise HTTPException(status_code=400, detail="Company name already exists")
        updates["company_name"] = new_name
    updates["updated_at"] = now_iso()
    await db.companies.update_one({"id": company_id}, {"$set": updates})
    # Propagate name change to denormalized tickets/devices
    if "company_name" in updates:
        await db.tickets.update_many({"company_id": company_id},
                                       {"$set": {"company_name": updates["company_name"]}})
        await db.devices.update_many({"company_id": company_id},
                                       {"$set": {"company_name": updates["company_name"]}})
    return await db.companies.find_one({"id": company_id}, {"_id": 0})


@api.delete("/companies/{company_id}", dependencies=[Depends(require_admin)])
async def delete_company(company_id: str):
    # Prevent deletion if open tickets exist
    open_tix = await db.tickets.count_documents({
        "company_id": company_id,
        "status": {"$nin": ["closed", "rejected"]},
    })
    if open_tix > 0:
        raise HTTPException(status_code=400,
                             detail=f"Cannot delete: {open_tix} active tickets")
    res = await db.companies.delete_one({"id": company_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Company not found")
    return {"ok": True}


# ---------- DEVICES ----------
@api.get("/devices")
async def list_devices(q: Optional[str] = None,
                        company_id: Optional[str] = None,
                        user=Depends(get_current_user)):
    query: Dict[str, Any] = {}
    if company_id:
        query["company_id"] = company_id
    if q:
        query["$or"] = [
            {"serial_number": {"$regex": q, "$options": "i"}},
            {"device_id": {"$regex": q, "$options": "i"}},
            {"brand": {"$regex": q, "$options": "i"}},
            {"model": {"$regex": q, "$options": "i"}},
            {"device_name": {"$regex": q, "$options": "i"}},
        ]
    devices = await db.devices.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return devices


@api.get("/devices/{device_id}")
async def get_device(device_id: str, user=Depends(get_current_user)):
    device = await db.devices.find_one({"device_id": device_id}, {"_id": 0})
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    tickets = await db.tickets.find(
        {"device_id": device_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    for t in tickets:
        if t.get("assigned_engineer_id"):
            eng = await db.users.find_one(
                {"id": t["assigned_engineer_id"]}, {"_id": 0, "name": 1}
            )
            t["engineer_name"] = eng["name"] if eng else None
    return {"device": device, "history": tickets}


# ---------- TICKETS ----------
async def _get_or_create_device(company: dict, d: DeviceCreate) -> dict:
    serial = (d.serial_number or "").strip() or None
    existing = None
    if serial:
        existing = await db.devices.find_one({"serial_number": serial,
                                                "company_id": company["id"]})
    if existing:
        updates = {}
        if d.warranty_status != existing.get("warranty_status"):
            updates["warranty_status"] = d.warranty_status
        if d.warranty_expiry and d.warranty_expiry != existing.get("warranty_expiry"):
            updates["warranty_expiry"] = d.warranty_expiry
        if updates:
            updates["updated_at"] = now_iso()
            await db.devices.update_one({"device_id": existing["device_id"]},
                                         {"$set": updates})
            existing.update(updates)
        existing.pop("_id", None)
        return existing
    dev_id = await next_device_id()
    doc = {
        "id": new_id(),
        "device_id": dev_id,
        "company_id": company["id"],
        "company_name": company["company_name"],
        "serial_number": serial,
        "brand": d.brand,
        "model": d.model,
        "device_name": d.device_name or f"{d.brand} {d.model}",
        "device_type": d.device_type,
        "warranty_status": d.warranty_status,
        "warranty_expiry": d.warranty_expiry,
        "purchase_date": d.purchase_date,
        "notes": d.notes,
        "is_deleted": False,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.devices.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _log_status(ticket_id: str, actor: dict, old_status: Optional[str],
                       new_status: str, remarks: Optional[str] = None):
    await db.ticket_status_logs.insert_one({
        "id": new_id(),
        "ticket_id": ticket_id,
        "old_status": old_status,
        "new_status": new_status,
        "changed_by": actor["id"],
        "changed_by_name": actor.get("name"),
        "changed_by_role": actor.get("role"),
        "remarks": remarks,
        "timestamp": now_iso(),
    })


async def _ticket_full(ticket: dict) -> dict:
    if not ticket:
        return ticket
    ticket = clean(ticket)
    if ticket.get("device_id"):
        device = await db.devices.find_one({"device_id": ticket["device_id"]},
                                            {"_id": 0})
        ticket["device"] = device
    if ticket.get("company_id"):
        company = await db.companies.find_one({"id": ticket["company_id"]},
                                                {"_id": 0})
        ticket["company"] = company
    if ticket.get("assigned_engineer_id"):
        eng = await db.users.find_one({"id": ticket["assigned_engineer_id"]},
                                       {"_id": 0, "password_hash": 0})
        ticket["engineer"] = eng
    return ticket


@api.post("/tickets", dependencies=[Depends(require_admin)])
async def create_ticket(payload: TicketCreate, admin=Depends(require_admin)):
    company = await db.companies.find_one({"id": payload.company_id})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if company.get("status") == "inactive":
        raise HTTPException(status_code=400, detail="Company is inactive")

    device = await _get_or_create_device(company, payload.device)
    ticket_no = await next_ticket_number()
    ticket = {
        "id": new_id(),
        "ticket_no": ticket_no,
        "ticket_number": ticket_no,  # backwards-compat alias
        "company_id": company["id"],
        "company_name": company["company_name"],
        "customer_name": payload.customer_name or company.get("contact_person"),
        "customer_phone": payload.customer_phone or company.get("phone"),
        "customer_company": company["company_name"],
        "customer_address": company.get("address"),
        "contact_source": payload.contact_source,
        "issue_description": payload.issue_description,
        "problem_description": payload.issue_description,  # legacy alias
        "priority": payload.priority,
        "device_id": device["device_id"],
        "device_name": device.get("device_name"),
        "serial_number": device.get("serial_number"),
        "status": "open",
        "assigned_engineer_id": None,
        "assigned_engineer_name": None,
        "engineer_notes": None,
        "admin_notes": None,
        "created_by": admin["id"],
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "completed_at": None,
        "engineer_location": None,
        "approved": False,
        "report_id": None,
        "pdf_path": None,
    }
    await db.tickets.insert_one(ticket)
    await _log_status(ticket["id"], admin, None, "open",
                       f"Ticket {ticket_no} created for {company['company_name']}")
    return await _ticket_full(ticket)


@api.get("/tickets")
async def list_tickets(
    status: Optional[str] = None,
    company_id: Optional[str] = None,
    mine: bool = False,
    user=Depends(get_current_user),
):
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status
    if company_id:
        q["company_id"] = company_id
    if user["role"] == "engineer" or mine:
        q["assigned_engineer_id"] = user["id"]
    tickets = await db.tickets.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    for t in tickets:
        if t.get("device_id"):
            device = await db.devices.find_one(
                {"device_id": t["device_id"]},
                {"_id": 0, "brand": 1, "model": 1, "device_id": 1,
                 "warranty_status": 1, "device_name": 1}
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
    full = await _ticket_full(ticket)
    full["status_logs"] = await db.ticket_status_logs.find(
        {"ticket_id": ticket_id}, {"_id": 0}
    ).sort("timestamp", -1).to_list(200)
    # back-compat alias for older UI
    full["activity"] = [
        {"id": s["id"], "ticket_id": s["ticket_id"],
         "action": f"status_{s['new_status']}",
         "actor_id": s.get("changed_by"),
         "actor_name": s.get("changed_by_name"),
         "actor_role": s.get("changed_by_role"),
         "details": s.get("remarks"),
         "timestamp": s["timestamp"]}
        for s in full["status_logs"]
    ]
    if full.get("report_id"):
        rep = await db.service_reports.find_one({"id": full["report_id"]}, {"_id": 0})
        full["report"] = rep
    if full.get("device_id"):
        history = await db.tickets.find(
            {"device_id": full["device_id"], "id": {"$ne": ticket_id}},
            {"_id": 0, "ticket_no": 1, "ticket_number": 1, "status": 1,
             "created_at": 1, "issue_description": 1, "problem_description": 1,
             "assigned_engineer_id": 1}
        ).sort("created_at", -1).to_list(20)
        full["device_history"] = history
    return full


@api.post("/tickets/{ticket_id}/assign", dependencies=[Depends(require_admin)])
async def assign_ticket(ticket_id: str, payload: TicketAssign,
                          admin=Depends(require_admin)):
    eng = await db.users.find_one(
        {"id": payload.engineer_id, "role": "engineer", "is_active": True}
    )
    if not eng:
        raise HTTPException(status_code=404, detail="Engineer not found")
    ticket = await db.tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    old = ticket["status"]
    await db.tickets.update_one(
        {"id": ticket_id},
        {"$set": {
            "assigned_engineer_id": payload.engineer_id,
            "assigned_engineer_name": eng["name"],
            "status": "assigned",
            "updated_at": now_iso(),
        }}
    )
    await _log_status(ticket_id, admin, old, "assigned",
                        f"Assigned to {eng['name']}")
    await db.notifications.insert_one({
        "id": new_id(),
        "user_id": payload.engineer_id,
        "title": "New ticket assigned",
        "message": f"Ticket {ticket['ticket_no']} has been assigned to you",
        "type": "ticket_assigned",
        "ticket_id": ticket_id,
        "read_status": False,
        "read": False,
        "created_at": now_iso(),
    })
    return await _ticket_full(await db.tickets.find_one({"id": ticket_id}, {"_id": 0}))


@api.post("/tickets/{ticket_id}/status")
async def update_status(ticket_id: str, payload: StatusUpdate,
                          user=Depends(get_current_user)):
    ticket = await db.tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user["role"] == "engineer" and ticket.get("assigned_engineer_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your ticket")

    # Reject is special — sends back to open pool
    if payload.status == "rejected":
        new_status = "open"
        await db.tickets.update_one(
            {"id": ticket_id},
            {"$set": {
                "status": "open",
                "assigned_engineer_id": None,
                "assigned_engineer_name": None,
                "reject_reason": payload.reject_reason,
                "updated_at": now_iso(),
            }}
        )
        await _log_status(ticket_id, user, ticket["status"], "rejected",
                            payload.reject_reason)
        await db.notifications.insert_one({
            "id": new_id(), "user_id": "admin", "ticket_id": ticket_id,
            "title": f"Ticket {ticket['ticket_no']} rejected",
            "message": payload.reject_reason or "",
            "type": "status_rejected", "read_status": False, "read": False,
            "created_at": now_iso(),
        })
        return await _ticket_full(await db.tickets.find_one({"id": ticket_id}, {"_id": 0}))

    # Block report_generated unless service_report exists
    if payload.status == "report_generated":
        report = await db.service_reports.find_one({"ticket_id": ticket_id})
        if not report:
            raise HTTPException(status_code=400, detail="Cannot mark report_generated without a service report")

    updates = {"status": payload.status, "updated_at": now_iso()}
    if payload.status in ("closed", "report_generated"):
        updates["completed_at"] = now_iso()
    if payload.latitude is not None and payload.longitude is not None:
        updates["engineer_location"] = {
            "lat": payload.latitude, "lng": payload.longitude,
            "updated_at": now_iso(),
        }
    await db.tickets.update_one({"id": ticket_id}, {"$set": updates})
    await _log_status(ticket_id, user, ticket["status"], payload.status,
                        payload.note)
    if payload.status in ("accepted", "resolved", "completed_with_signature",
                          "report_generated", "closed"):
        await db.notifications.insert_one({
            "id": new_id(), "user_id": "admin", "ticket_id": ticket_id,
            "title": f"Ticket {ticket['ticket_no']} → {payload.status}",
            "message": payload.note or "",
            "type": f"status_{payload.status}",
            "read_status": False, "read": False, "created_at": now_iso(),
        })
    return await _ticket_full(await db.tickets.find_one({"id": ticket_id}, {"_id": 0}))


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
    """
    Submit signed service report.
    Security: only assigned engineer; signature required.
    Flow: ticket → completed_with_signature → PDF generated → report_generated.
    """
    ticket = await db.tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.get("assigned_engineer_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your ticket")
    if not payload.customer_signature:
        raise HTTPException(status_code=400, detail="Customer signature is required")

    # Mark completed_with_signature
    await db.tickets.update_one({"id": ticket_id}, {
        "$set": {"status": "completed_with_signature", "updated_at": now_iso()}
    })
    await _log_status(ticket_id, user, ticket["status"],
                        "completed_with_signature",
                        "Customer signature captured")

    device = await db.devices.find_one(
        {"device_id": ticket.get("device_id")}, {"_id": 0}
    )
    company = await db.companies.find_one(
        {"id": ticket.get("company_id")}, {"_id": 0}
    ) if ticket.get("company_id") else None
    engineer = await db.users.find_one(
        {"id": user["id"]}, {"_id": 0, "password_hash": 0}
    )

    report_id = new_id()
    report = {
        "id": report_id,
        "ticket_id": ticket_id,
        "ticket_no": ticket["ticket_no"],
        "engineer_id": user["id"],
        "engineer_name": engineer["name"],
        "customer_name": payload.customer_signed_name or ticket.get("customer_name"),
        "engineer_notes": payload.engineer_notes,
        "resolution_summary": payload.resolution_summary or payload.engineer_notes,
        "parts_used": [p.model_dump() for p in payload.parts_used],
        "before_images": payload.before_images,
        "after_images": payload.after_images,
        "customer_signature": payload.customer_signature,
        "customer_signed_name": payload.customer_signed_name,
        "signed_at": now_iso(),
        "generated_at": now_iso(),
        "pdf_url": None,
        "pdf_path": None,
        # Aliases for PDF builder (legacy field names)
        "work_notes": payload.engineer_notes,
        "photos_before": payload.before_images,
        "photos_after": payload.after_images,
    }
    # Generate PDF
    pdf_path = None
    try:
        # Inject company info into the pdf input
        ticket_for_pdf = {**ticket, "report_id": report_id,
                          "company": company}
        pdf_bytes = build_service_report_pdf(
            ticket=ticket_for_pdf, device=device,
            engineer=engineer, report=report,
        )
        pdf_path = f"reports/{ticket['ticket_no']}-{report_id}.pdf"
        put_object(pdf_path, pdf_bytes, "application/pdf")
        await db.attachments.insert_one({
            "id": new_id(),
            "ticket_id": ticket_id,
            "uploaded_by": user["id"],
            "file_name": f"{ticket['ticket_no']}.pdf",
            "file_type": "application/pdf",
            "file_url": f"/api/files/{pdf_path}",
            "storage_path": pdf_path,
            "size": len(pdf_bytes),
            "is_deleted": False,
            "uploaded_at": now_iso(),
        })
        report["pdf_url"] = f"/api/files/{pdf_path}"
        report["pdf_path"] = pdf_path
    except Exception as e:
        logger.error(f"PDF generation/upload failed: {e}")

    # Persist service report
    await db.service_reports.replace_one(
        {"ticket_id": ticket_id}, report, upsert=True
    )

    # Update ticket → report_generated
    next_status = "report_generated" if pdf_path else "completed_with_signature"
    await db.tickets.update_one(
        {"id": ticket_id},
        {"$set": {
            "report_id": report_id,
            "status": next_status,
            "pdf_path": pdf_path,
            "pdf_url": report.get("pdf_url"),
            "updated_at": now_iso(),
            "completed_at": now_iso() if next_status == "report_generated" else None,
        }}
    )
    await _log_status(ticket_id, user, "completed_with_signature",
                        next_status, "Service report generated")
    await db.notifications.insert_one({
        "id": new_id(), "user_id": "admin", "ticket_id": ticket_id,
        "title": f"Service report ready: {ticket['ticket_no']}",
        "message": "Customer-signed PDF is ready for review.",
        "type": "report_ready", "read_status": False, "read": False,
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
            "status": "closed",
            "updated_at": now_iso(),
            "completed_at": now_iso(),
        }}
    )
    await _log_status(ticket_id, admin, ticket["status"], "closed",
                        "Service report approved and ticket closed")
    return await _ticket_full(await db.tickets.find_one({"id": ticket_id}, {"_id": 0}))


# ---------- REPORTS ----------
@api.get("/reports/{ticket_id}")
async def get_report(ticket_id: str, user=Depends(get_current_user)):
    ticket = await db.tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user["role"] == "engineer" and ticket.get("assigned_engineer_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not your ticket")
    report = await db.service_reports.find_one({"ticket_id": ticket_id}, {"_id": 0})
    if not report:
        raise HTTPException(status_code=404, detail="No report for this ticket")
    return report


# ---------- FILES (local storage server) ----------
@api.get("/files/{path:path}")
async def serve_file(path: str, request: Request):
    """Serve files from local storage. Auth via header or ?auth=token."""
    from auth import extract_token
    token = extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token)
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    attachment = await db.attachments.find_one({
        "storage_path": path, "is_deleted": False
    })
    if attachment:
        if user["role"] == "engineer":
            ticket = await db.tickets.find_one({"id": attachment["ticket_id"]})
            if not ticket or ticket.get("assigned_engineer_id") != user["id"]:
                raise HTTPException(status_code=403, detail="Access denied")
    try:
        data, _ = get_object(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    content_type = "application/pdf" if path.endswith(".pdf") else \
                   "image/jpeg" if path.endswith((".jpg", ".jpeg")) else \
                   "image/png" if path.endswith(".png") else \
                   "application/octet-stream"
    filename = os.path.basename(path)
    return Response(content=data, media_type=content_type, headers={
        "Content-Disposition": f'inline; filename="{filename}"'
    })


# Back-compat endpoint
@api.get("/tickets/{ticket_id}/pdf")
async def get_ticket_pdf(ticket_id: str, request: Request):
    from auth import extract_token
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
        data, _ = get_object(ticket["pdf_path"])
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="PDF file missing")
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename=\"{ticket['ticket_no']}.pdf\""
    })


# ---------- NOTIFICATIONS ----------
@api.get("/notifications")
async def list_notifications(user=Depends(get_current_user)):
    target = "admin" if user["role"] == "admin" else user["id"]
    notes = await db.notifications.find(
        {"user_id": target}, {"_id": 0}
    ).sort("created_at", -1).limit(50).to_list(50)
    # Normalize read flag for older docs
    for n in notes:
        if "read" not in n:
            n["read"] = n.get("read_status", False)
    return notes


@api.post("/notifications/{nid}/read")
async def mark_read(nid: str, user=Depends(get_current_user)):
    await db.notifications.update_one(
        {"id": nid}, {"$set": {"read": True, "read_status": True}}
    )
    return {"ok": True}


# ---------- ATTENDANCE ----------
@api.post("/attendance/check-in")
async def check_in(payload: AttendanceAction, user=Depends(require_engineer)):
    today = date.today().isoformat()
    existing = await db.attendance.find_one({"engineer_id": user["id"], "date": today})
    if existing and existing.get("check_in_time"):
        raise HTTPException(status_code=400, detail="Already checked in today")
    doc = {
        "id": new_id(),
        "engineer_id": user["id"],
        "date": today,
        "check_in_time": now_iso(),
        "check_in_location": {"lat": payload.latitude, "lng": payload.longitude}
            if payload.latitude is not None else None,
        "check_out_time": None,
        "attendance_status": "present",
        "created_at": now_iso(),
    }
    if existing:
        await db.attendance.update_one({"id": existing["id"]}, {"$set": doc})
        doc["id"] = existing["id"]
    else:
        await db.attendance.insert_one(doc)
    # Back-compat alias fields
    doc["check_in"] = doc["check_in_time"]
    doc["check_out"] = doc["check_out_time"]
    doc.pop("_id", None)
    return doc


@api.post("/attendance/check-out")
async def check_out(payload: AttendanceAction, user=Depends(require_engineer)):
    today = date.today().isoformat()
    existing = await db.attendance.find_one({"engineer_id": user["id"], "date": today})
    if not existing or not existing.get("check_in_time"):
        raise HTTPException(status_code=400, detail="Not checked in")
    if existing.get("check_out_time"):
        raise HTTPException(status_code=400, detail="Already checked out")
    await db.attendance.update_one(
        {"id": existing["id"]},
        {"$set": {
            "check_out_time": now_iso(),
            "check_out_location": {"lat": payload.latitude, "lng": payload.longitude}
                if payload.latitude is not None else None,
        }}
    )
    doc = await db.attendance.find_one({"id": existing["id"]}, {"_id": 0})
    doc["check_in"] = doc.get("check_in_time")
    doc["check_out"] = doc.get("check_out_time")
    return doc


@api.get("/attendance/today")
async def attendance_today(user=Depends(require_engineer)):
    today = date.today().isoformat()
    doc = await db.attendance.find_one({"engineer_id": user["id"], "date": today},
                                         {"_id": 0})
    if doc:
        doc["check_in"] = doc.get("check_in_time")
        doc["check_out"] = doc.get("check_out_time")
    return doc or {}


@api.get("/attendance/history")
async def attendance_history(user=Depends(require_engineer)):
    docs = await db.attendance.find(
        {"engineer_id": user["id"]}, {"_id": 0}
    ).sort("date", -1).limit(60).to_list(60)
    for d in docs:
        d["check_in"] = d.get("check_in_time")
        d["check_out"] = d.get("check_out_time")
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
         "reached_site", "in_progress", "resolved",
         "completed_with_signature", "report_generated"]
    )
    total_eng = await db.users.count_documents({"role": "engineer", "is_active": True})
    available = await db.users.count_documents(
        {"role": "engineer", "is_active": True, "is_available": True}
    )
    total_co = await db.companies.count_documents({"status": "active"})
    logs = await db.ticket_status_logs.find({}, {"_id": 0}) \
        .sort("timestamp", -1).limit(15).to_list(15)
    # back-compat shape
    recent = [{
        "id": l["id"],
        "actor_id": l.get("changed_by"),
        "actor_name": l.get("changed_by_name"),
        "actor_role": l.get("changed_by_role"),
        "action": f"status_{l['new_status']}",
        "details": l.get("remarks"),
        "timestamp": l["timestamp"],
    } for l in logs]
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
        "companies": {"active": total_co},
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
        {"assigned_engineer_id": eid,
         "status": {"$in": ["closed", "report_generated", "completed_with_signature"]}}
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
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    per_day = []
    for d in days:
        c = await db.tickets.count_documents({
            "created_at": {"$gte": d, "$lt": d + "T99"}
        })
        per_day.append({"date": d, "count": c})
    engineers = await db.users.find(
        {"role": "engineer"}, {"_id": 0, "id": 1, "name": 1}
    ).to_list(200)
    perf = []
    for e in engineers:
        completed = await db.tickets.count_documents(
            {"assigned_engineer_id": e["id"],
             "status": {"$in": ["closed", "report_generated"]}}
        )
        active = await db.tickets.count_documents(
            {"assigned_engineer_id": e["id"],
             "status": {"$nin": ["closed", "rejected", "report_generated"]}}
        )
        perf.append({"name": e["name"], "completed": completed, "active": active})
    perf.sort(key=lambda x: -x["completed"])
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
    pipeline2 = [{"$group": {"_id": "$device_id", "count": {"$sum": 1}}}]
    brand_counts: Dict[str, int] = {}
    async for row in db.tickets.aggregate(pipeline2):
        device = await db.devices.find_one({"device_id": row["_id"]}, {"_id": 0})
        if device:
            b = device.get("brand", "Unknown")
            brand_counts[b] = brand_counts.get(b, 0) + row["count"]
    brand_trend = [{"brand": k, "tickets": v} for k, v in
                   sorted(brand_counts.items(), key=lambda x: -x[1])][:8]
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
            "ticket_number": t.get("ticket_no") or t.get("ticket_number"),
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
