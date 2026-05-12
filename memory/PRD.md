# Plutus Ventures – IT Service Management System

## Original Problem Statement
Complete IT Service Management System: Admin web panel + Engineer mobile-first PWA. Branded for Plutus Ventures ("Partnering Your IT Landscape"). Now enhanced with Company Management module and a hardened digital-signature → PDF service report workflow.

## Architecture
- **Backend**: FastAPI + MongoDB (Motor async). 700+ line modular endpoints under `/api`.
- **Frontend**: React 19 + React Router 7 + Tailwind + shadcn/ui + Framer Motion.
- **Storage**: Local filesystem at `STORAGE_PATH` (default `/app/backend/storage`). Replaces previous cloud-storage dependency. Compatible with Render, Railway, AWS, Vercel volumes.
- **PDF**: ReportLab + qrcode. Letterhead with Plutus Ventures logo, tagline, and QR code containing ticket number.
- **Auth**: JWT (12h) + email/password + 6-digit OTP (mock returns dev_otp in response).
- **Maps**: Leaflet + CartoDB Positron tiles.

## Collections
- `users` – admin & engineer accounts (name, email, role, password_hash, employee_id, designation, address, skills, status, last_login, timestamps)
- `companies` – company_name (unique), company_code (auto CMP-XXXX), contact_person, phone, email, address, gst_number, city, state, pincode, status, timestamps
- `devices` – device_id (auto DEV-YYYY-XXXX), company_id, company_name, brand, model, serial_number, warranty_status/expiry, purchase_date, device_type, timestamps
- `tickets` – ticket_no (TKT-YYYY-XXXX), company_id, company_name, device fields, status, assigned_engineer_id/name, priority, engineer_notes, admin_notes, timestamps
- `ticket_status_logs` – ticket_id, old_status, new_status, changed_by, changed_by_name/role, remarks, timestamp (audit trail)
- `service_reports` – ticket_id (unique), engineer_id/name, customer_name, customer_signature, engineer_notes, resolution_summary, parts_used, before_images, after_images, pdf_url, generated_at
- `attachments` – ticket_id, uploaded_by, file_name, file_type, file_url, storage_path, size, is_deleted, uploaded_at
- `attendance` – engineer_id, date, check_in_time/out_time, locations, attendance_status
- `notifications` – user_id, title, message, type, read_status, ticket_id, created_at
- `counters` – `{_id, sequence_value}` for ticket / device / company sequence

## Indexes
- `users.email` (unique), `users.employee_id` (sparse)
- `companies.company_name` (unique), `companies.company_code` (unique), `companies.status`
- `tickets.ticket_no` (unique sparse), `tickets.status`, `tickets.assigned_engineer_id`, `tickets.company_id`
- `devices.device_id` (unique), `devices.serial_number` (sparse), `devices.company_id`
- `ticket_status_logs.ticket_id`, `ticket_status_logs.timestamp`
- `service_reports.ticket_id` (unique)
- `attachments.ticket_id`

## Ticket Status Workflow
`open → assigned → accepted → travelling → reached_site → in_progress → completed_with_signature → report_generated → closed`
(plus `rejected` which returns ticket to open pool)

## What's Been Implemented

### Iteration 1 (initial MVP)
- Full admin + engineer experience, login with OTP, ticket lifecycle, PDFs, attendance, analytics
- 33/33 backend tests pass • 100% frontend pass

### Iteration 2 (this update)
- **Company Management**: full CRUD (GET / POST / PUT / DELETE), pagination, search by name/code/contact/GST/city, status filter, prevent duplicate company names, propagate name change to tickets/devices
- **Admin pages**: /admin/companies, /admin/companies/new, /admin/companies/:id (inline edit + status toggle + tickets/devices listing)
- **Ticket integration**: company_id required, auto-fill customer name/phone/address from company contact details, "New ticket" CTA on company detail page
- **New statuses**: completed_with_signature, report_generated, closed
- **Service report flow**: signature required → ticket → completed_with_signature → PDF generated → report_generated; approve transitions to closed
- **PDF**: now includes QR code (ticket number) + Plutus letterhead with embedded logo
- **Storage**: switched to local filesystem (removed cloud storage dependency)
- **Collections**: ticket_status_logs replaces activity; attachments replaces files
- **Engineer fields**: employee_id, designation, address added
- **Env files**: `backend/.env.example` and `frontend/.env.example`
- 28/29 backend tests pass • 100% frontend pass

## Prioritized Backlog
### P1
- Split monolithic server.py into routers (auth, companies, tickets, reports, dashboard)
- Real OTP delivery (Resend / SendGrid / Twilio) for production
- Brute-force lockout on /auth/login
- Path traversal hardening in storage_client (reject `..`)

### P2
- Real-time WebSocket notifications (replace polling)
- Customer self-service portal (public ticket creation)
- Native push via FCM
- SLA & escalation rules
- Multi-tenant support / per-company branding upload
- Customer-shareable PDF link with token-less view (signed URL)

## Test Credentials
- Admin (primary): `admin@plutusventures.com` / `admin123`
- Admin (legacy): `admin@serviceops.com` / `admin123`
- Engineer (primary): `engineer@plutusventures.com` / `engineer123`
- Engineer (legacy): `engineer@serviceops.com` / `engineer123`
- OTP: returned in `dev_otp` field of `/api/auth/login` and displayed in UI under the OTP input

## Deployment
Compatible with MongoDB Atlas + any of: Render, Railway, AWS, Vercel (serverless functions for backend, S3/GCS mount for storage, or fall back to a persistent volume).

Required env:
- Backend: `MONGO_URL`, `DB_NAME`, `JWT_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `COMPANY_NAME`, `STORAGE_PATH`
- Frontend: `REACT_APP_BACKEND_URL`
