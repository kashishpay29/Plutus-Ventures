# ServiceOps – IT Service Management System

## Original Problem Statement
Complete IT Service Management System with Admin Web Panel + Engineer Mobile-First PWA.

## Architecture
- **Backend**: FastAPI + MongoDB (Motor async driver)
- **Frontend**: React 19 + React Router 7 + Tailwind + shadcn/ui
- **Storage**: Emergent Object Storage (PDFs)
- **Maps**: Leaflet + CartoDB Positron tiles
- **PDF**: ReportLab with company letterhead
- **Auth**: JWT (12h) + email/password + 6-digit OTP (mock for MVP; OTP returned in API response)

## User Personas
- **Admin**: Creates tickets, assigns engineers, tracks live, approves reports, runs analytics.
- **Engineer**: Receives assignments, updates status, captures photos/parts/signature on-site, generates PDF.

## Core Requirements (static)
- Two roles: admin (full access), engineer (own tickets only)
- 8-status ticket lifecycle: open→assigned→accepted→travelling→reached_site→in_progress→resolved→completed
- Auto-generated Device IDs (DEV-YYYY-XXXX) when no serial number
- All status changes logged with user + timestamp
- PDFs include letterhead + customer signature
- Real-time engineer locations on map
- Notifications for assignment / status / report-ready
- Mobile-responsive PWA experience

## What's Been Implemented (2026-02)
### Backend
- `/api/auth/login` + `/api/auth/verify-otp` (mock OTP returned)
- `/api/auth/me`
- `/api/tickets` CRUD + filters + status updates + assign + approve + PDF
- `/api/tickets/{id}/report` generates PDF and uploads to Object Storage
- `/api/engineers` CRUD (admin) + listing (all)
- `/api/devices` search + history
- `/api/dashboard/admin` & `/api/dashboard/engineer`
- `/api/analytics` (per-day, engineer perf, repeat complaints, brand trend, warranty alerts)
- `/api/live-locations` (admin)
- `/api/notifications` (per-user feed)
- `/api/attendance/check-in` / `check-out` / `today` / `history`

### Frontend
- Login: 2-step (creds → OTP) with Chivo branding + corporate background
- **Admin (desktop)**: Sidebar layout
  - Dashboard with live stats, status pipeline, recent activity, warranty alerts
  - Ticket board (kanban with 8 columns) + list view + search
  - Ticket detail: customer/device/warranty cards, engineer assign drawer, activity log, photos, signature display, PDF download, approve & close
  - Ticket create form
  - Engineers CRUD (cards) with skills, active/available toggles
  - Devices search + history modal
  - Live map (Leaflet) + dispatched list
  - Analytics (line + bar + pie + repeat list)
- **Engineer (mobile-first PWA)**: Bottom nav layout
  - Home with stats + active tickets
  - Tickets list with tabs (Active/Resolved/Done)
  - Ticket detail with status-progression CTA button (Accept→Travelling→Reached→In Progress→Submit Report)
  - Reject with reason
  - Report drawer: work notes, parts list, before/after photos (camera capture + compression), signature pad, customer name
  - Auto-PDF generation + Object Storage upload
  - Attendance check-in/out with geolocation
  - Profile with skills & completed history

## Prioritized Backlog
### P1
- Real-time WebSocket notifications (currently polling 10–15s)
- Native push notifications via FCM
- Multi-tenant support / company branding upload

### P2
- Customer portal (self-serve ticket creation)
- SLA breach alerts + escalation
- Mobile camera vs gallery split
- Parts inventory tracking
- Export analytics as CSV

## Test Credentials
- Admin: `admin@serviceops.com` / `admin123`
- Engineer: `engineer@serviceops.com` / `engineer123`
- OTP is returned in `dev_otp` field of `/api/auth/login` response (mock mode)
