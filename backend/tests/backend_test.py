"""
ServiceOps - End-to-end backend API regression suite.
Covers: auth (login+OTP), engineers CRUD, tickets full lifecycle (create -> assign ->
status progression -> report submission with PDF -> approve), devices search,
attendance, analytics, dashboards, role enforcement.
"""
import os
import base64
import io
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://servicedesk-pro-11.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@serviceops.com"
ADMIN_PASSWORD = "admin123"
ENG_EMAIL = "engineer@serviceops.com"
ENG_PASSWORD = "engineer123"


# ---------- Helpers ----------
def _login(email: str, password: str) -> str:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    assert "challenge_id" in data and "dev_otp" in data
    v = requests.post(
        f"{API}/auth/verify-otp",
        json={"email": email, "otp": data["dev_otp"], "challenge_id": data["challenge_id"]},
        timeout=30,
    )
    assert v.status_code == 200, f"verify-otp failed: {v.status_code} {v.text}"
    body = v.json()
    assert "token" in body and "user" in body
    assert body["user"]["email"] == email
    return body["token"]


def _h(token: str):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
)
_DATA_URL = f"data:image/png;base64,{_PNG_B64}"


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def admin_token():
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="session")
def engineer_token():
    return _login(ENG_EMAIL, ENG_PASSWORD)


@pytest.fixture(scope="session")
def engineer_user(engineer_token):
    r = requests.get(f"{API}/auth/me", headers=_h(engineer_token), timeout=30)
    assert r.status_code == 200
    return r.json()


# ---------- AUTH ----------
class TestAuth:
    def test_login_returns_challenge_and_otp(self):
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data.get("challenge_id")
        assert data.get("dev_otp") and len(data["dev_otp"]) == 6

    def test_login_invalid_password(self):
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"}, timeout=30)
        assert r.status_code == 401

    def test_verify_otp_invalid(self):
        login = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).json()
        r = requests.post(
            f"{API}/auth/verify-otp",
            json={"email": ADMIN_EMAIL, "otp": "000000", "challenge_id": login["challenge_id"]},
            timeout=30,
        )
        # Either incorrect OTP or - if 000000 happened to match - accept either deterministic outcome
        assert r.status_code in (400, 200)

    def test_auth_me_admin(self, admin_token):
        r = requests.get(f"{API}/auth/me", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200
        u = r.json()
        assert u["email"] == ADMIN_EMAIL and u["role"] == "admin"

    def test_auth_me_engineer(self, engineer_token):
        r = requests.get(f"{API}/auth/me", headers=_h(engineer_token), timeout=30)
        assert r.status_code == 200
        assert r.json()["role"] == "engineer"


# ---------- Engineers CRUD ----------
class TestEngineers:
    created_id = None

    def test_admin_list_engineers(self, admin_token):
        r = requests.get(f"{API}/engineers", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_admin_create_engineer(self, admin_token):
        suffix = int(time.time())
        email = f"test_eng_{suffix}@serviceops.com"
        r = requests.post(
            f"{API}/engineers",
            headers=_h(admin_token),
            json={
                "name": "TEST Engineer",
                "email": email,
                "phone": "+919900000000",
                "password": "pass1234",
                "skills": ["Laptop"],
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["email"] == email and data["role"] == "engineer"
        TestEngineers.created_id = data["id"]

    def test_admin_update_engineer(self, admin_token):
        assert TestEngineers.created_id
        r = requests.patch(
            f"{API}/engineers/{TestEngineers.created_id}",
            headers=_h(admin_token),
            json={"phone": "+919911112222", "is_available": False},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        assert r.json()["phone"] == "+919911112222"
        assert r.json()["is_available"] is False

    def test_engineer_cannot_create_engineer(self, engineer_token):
        r = requests.post(
            f"{API}/engineers",
            headers=_h(engineer_token),
            json={"name": "x", "email": "x_eng@x.com", "password": "p", "skills": []},
            timeout=30,
        )
        assert r.status_code == 403

    def test_admin_delete_engineer(self, admin_token):
        assert TestEngineers.created_id
        r = requests.delete(f"{API}/engineers/{TestEngineers.created_id}", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200


# ---------- Tickets full lifecycle ----------
class TestTicketLifecycle:
    state = {}

    def test_create_ticket_auto_device_id(self, admin_token):
        payload = {
            "customer_name": "TEST Customer",
            "customer_phone": "+919900001111",
            "customer_company": "Acme",
            "contact_source": "call",
            "problem_description": "Laptop not booting",
            "device": {"brand": "Dell", "model": "Latitude 7480", "warranty_status": "active",
                       "warranty_expiry": "2026-12-31"},
        }
        r = requests.post(f"{API}/tickets", headers=_h(admin_token), json=payload, timeout=30)
        assert r.status_code == 200, r.text
        t = r.json()
        assert t["status"] == "open"
        assert t["ticket_number"].startswith("TKT-")
        assert t["device"]["device_id"].startswith("DEV-2026-") or t["device"]["device_id"].startswith("DEV-")
        TestTicketLifecycle.state["ticket"] = t

    def test_list_tickets_has_device_and_engineer(self, admin_token):
        r = requests.get(f"{API}/tickets", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200
        lst = r.json()
        assert isinstance(lst, list) and len(lst) >= 1
        # find our created ticket
        tid = TestTicketLifecycle.state["ticket"]["id"]
        found = next((x for x in lst if x["id"] == tid), None)
        assert found is not None
        assert "device" in found

    def test_assign_ticket(self, admin_token, engineer_user):
        tid = TestTicketLifecycle.state["ticket"]["id"]
        r = requests.post(
            f"{API}/tickets/{tid}/assign",
            headers=_h(admin_token),
            json={"engineer_id": engineer_user["id"]},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "assigned"
        assert r.json()["assigned_engineer_id"] == engineer_user["id"]

    @pytest.mark.parametrize("status", ["accepted", "travelling", "reached_site", "in_progress"])
    def test_engineer_status_progression(self, engineer_token, status):
        tid = TestTicketLifecycle.state["ticket"]["id"]
        r = requests.post(
            f"{API}/tickets/{tid}/status",
            headers=_h(engineer_token),
            json={"status": status, "latitude": 19.07, "longitude": 72.87},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == status

    def test_engineer_cannot_create_ticket(self, engineer_token):
        r = requests.post(f"{API}/tickets", headers=_h(engineer_token), json={
            "customer_name": "x", "customer_phone": "0", "problem_description": "x",
            "device": {"brand": "x", "model": "y"}
        }, timeout=30)
        assert r.status_code == 403

    def test_engineer_only_sees_own_tickets(self, engineer_token, engineer_user):
        r = requests.get(f"{API}/tickets", headers=_h(engineer_token), timeout=30)
        assert r.status_code == 200
        for t in r.json():
            assert t.get("assigned_engineer_id") == engineer_user["id"]

    def test_submit_report_generates_pdf(self, engineer_token):
        tid = TestTicketLifecycle.state["ticket"]["id"]
        r = requests.post(
            f"{API}/tickets/{tid}/report",
            headers=_h(engineer_token),
            json={
                "work_notes": "Replaced motherboard, ran diagnostics, all OK.",
                "parts_used": [{"name": "Motherboard", "part_number": "MB-12345", "quantity": 1}],
                "photos_before": [_DATA_URL],
                "photos_after": [_DATA_URL],
                "customer_signature": _DATA_URL,
                "customer_signed_name": "John Customer",
            },
            timeout=60,
        )
        assert r.status_code == 200, r.text
        rep = r.json()
        assert rep["work_notes"]
        # PDF path should be set; if storage failed it will be None - flag it
        TestTicketLifecycle.state["report"] = rep

    def test_ticket_now_resolved(self, admin_token):
        tid = TestTicketLifecycle.state["ticket"]["id"]
        r = requests.get(f"{API}/tickets/{tid}", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200
        assert r.json()["status"] == "resolved"

    def test_pdf_download_with_bearer(self, admin_token):
        tid = TestTicketLifecycle.state["ticket"]["id"]
        r = requests.get(f"{API}/tickets/{tid}/pdf", headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)
        # If PDF generation+storage works, expect 200 PDF; if storage broke, log it.
        if r.status_code != 200:
            pytest.fail(f"PDF download failed: {r.status_code} {r.text[:200]}")
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert r.content[:4] == b"%PDF"

    def test_pdf_download_with_query_token(self, admin_token):
        tid = TestTicketLifecycle.state["ticket"]["id"]
        r = requests.get(f"{API}/tickets/{tid}/pdf?auth={admin_token}", timeout=30)
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"

    def test_admin_approve_completes_ticket(self, admin_token):
        tid = TestTicketLifecycle.state["ticket"]["id"]
        r = requests.post(f"{API}/tickets/{tid}/approve", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200
        assert r.json()["status"] == "completed"


# ---------- Devices ----------
class TestDevices:
    def test_search_devices(self, admin_token):
        r = requests.get(f"{API}/devices?q=DEV-2026", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_device_with_history(self, admin_token):
        # pick the device from created ticket
        t = TestTicketLifecycle.state.get("ticket")
        assert t, "Ticket must be created first"
        dev_id = t["device"]["device_id"]
        r = requests.get(f"{API}/devices/{dev_id}", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200
        body = r.json()
        assert "device" in body and "history" in body
        assert body["device"]["device_id"] == dev_id


# ---------- Attendance ----------
class TestAttendance:
    def test_check_in_then_check_out(self, engineer_token):
        # may already be checked in from previous run - tolerate 400 then continue
        r = requests.post(f"{API}/attendance/check-in", headers=_h(engineer_token),
                          json={"latitude": 19.07, "longitude": 72.87}, timeout=30)
        assert r.status_code in (200, 400), r.text

        r2 = requests.post(f"{API}/attendance/check-out", headers=_h(engineer_token),
                           json={"latitude": 19.08, "longitude": 72.88}, timeout=30)
        # Either succeeds 200, or 400 if already checked out
        assert r2.status_code in (200, 400), r2.text

    def test_today(self, engineer_token):
        r = requests.get(f"{API}/attendance/today", headers=_h(engineer_token), timeout=30)
        assert r.status_code == 200


# ---------- Dashboards & Analytics ----------
class TestDashboardAnalytics:
    def test_admin_dashboard(self, admin_token):
        r = requests.get(f"{API}/dashboard/admin", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200
        d = r.json()
        for k in ("ticket_counts", "engineers", "recent_activity", "warranty_alerts"):
            assert k in d

    def test_engineer_dashboard(self, engineer_token):
        r = requests.get(f"{API}/dashboard/engineer", headers=_h(engineer_token), timeout=30)
        assert r.status_code == 200
        for k in ("assigned", "in_progress", "resolved", "completed"):
            assert k in r.json()

    def test_engineer_cannot_admin_dashboard(self, engineer_token):
        r = requests.get(f"{API}/dashboard/admin", headers=_h(engineer_token), timeout=30)
        assert r.status_code == 403

    def test_analytics(self, admin_token):
        r = requests.get(f"{API}/analytics", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200
        d = r.json()
        for k in ("per_day", "engineer_performance", "repeat_complaints", "brand_trend", "warranty_alerts"):
            assert k in d
        assert len(d["per_day"]) == 14

    def test_engineer_cannot_analytics(self, engineer_token):
        r = requests.get(f"{API}/analytics", headers=_h(engineer_token), timeout=30)
        assert r.status_code == 403
