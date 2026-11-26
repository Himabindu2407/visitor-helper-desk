# Visitor Helper Desk — Phase 3: OTP Verification (Development Mode)
SynthoQuest-branded visitor registration form with backend OTP request and verification.

## Features
- Backend form validation
- 6-digit OTP generation
- OTP expiry: 5 minutes
- Resend cooldown: 30 seconds
- Maximum 5 wrong attempts
- OTP verification endpoint
- Responsive OTP panel, verified state, and reset for next visitor
- Flask host set to 0.0.0.0 for LAN/mobile testing

## Development OTP only
This version does **not** send SMS. OTP is printed in the Flask terminal for local testing. Do not deploy publicly. Production SMS integration needs an SMS provider and credentials, plus secure rate limiting and OTP handling.

## Storage status
Registration details are kept only temporarily in the Flask session during OTP testing. No Google Form, database, or Excel saving is implemented yet.

## Windows setup
```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000 on the laptop. For mobile testing, use `http://LAPTOP-IP:5000` on the same trusted Wi-Fi.
Health endpoint: `/health`


Phase 3 OTP API endpoints are implemented in this fixed build. OTP is development-only and is printed in the Flask terminal; SMS delivery is not connected. Server binds to 0.0.0.0 for LAN mobile testing.


## Phase 4 — Visitor Data Storage

- OTP-verified visitor registrations are saved to a local SQLite database (`visitor_records.db`).
- Each saved visit gets a Visitor ID and local registration timestamp.
- Admin dashboard: `/admin` (session-protected login).
- CSV export: available from the admin dashboard (Excel can open the CSV).
- Admin password defaults to `change-this-admin-password` for local testing. Set `VHD_ADMIN_PASSWORD` before running for a custom password.
- Set `VHD_SECRET_KEY` to a stable random secret for deployments.
- This development build uses terminal OTP; real SMS and Google Sheets integration are not included.

**Development only:** Flask debug mode is enabled. Do not expose this server to the public internet. Change admin password and secrets before any deployment.

## Environment Configuration (Admin Password)

1. Copy `.env.example` to a new file named `.env` in this project root.
2. Open `.env` and set your own admin password and secret key.
3. Keep `.env` private; it is ignored by Git.
4. Install requirements with `pip install -r requirements.txt`.

Example `.env` values:

```env
VHD_ADMIN_PASSWORD=replace-with-your-own-password
VHD_SECRET_KEY=replace-with-a-long-random-secret
```

The project loads `.env` automatically using `python-dotenv`. The ZIP does not include an actual `.env` file for security.

## Phase 5 – Admin Dashboard Enhancements

Phase 5 adds the following admin dashboard features while retaining OTP registration and SQLite persistence:
- Summary statistics: total registrations, today's registrations, and visitor categories.
- Search across visitor ID, name, mobile, organization, email, person/department, and purpose.
- Filters by visitor type and registration date range.
- Pagination (20 records per page).
- Visitor detail page.
- Admin-protected visitor record deletion with confirmation and session CSRF token.
- CSV export honors active search and filters.

Run locally using the same virtual environment and `.env` setup described above. This remains a development prototype; before deployment, configure HTTPS, production secret/password values, a real SMS OTP provider, and additional security review.

## Phase 6.2 — Request Security & Input Validation

Phase 6.2 adds:
- Server-side validation for registration fields and allowed visitor types.
- Field-length limits and control-character checks.
- Email/mobile/name/person-to-meet/purpose validation.
- Basic markup character rejection in plain-text registration fields.
- Session-backed CSRF token checks for public OTP request, OTP verification, and reset-registration JSON requests.
- Search/export filter length bounds and date-format/range checks.

**Validation note:** Python syntax compilation was completed. Runtime endpoint tests could not be executed in this environment because Flask is not installed here. Run `pip install -r requirements.txt` in the project's virtual environment, then perform the checklist below.

### Phase 6.2 Test Checklist
- [ ] Valid registration -> OTP request accepted
- [ ] Missing/invalid CSRF header -> rejected
- [ ] Invalid mobile/email/visitor type -> rejected
- [ ] Oversized fields -> rejected
- [ ] HTML/script-like input in primary text fields -> rejected
- [ ] OTP verification without CSRF -> rejected
- [ ] Reset-registration without CSRF -> rejected
- [ ] Search filter date format/range checks
- [ ] Admin export filter date format/range checks
- [ ] Existing registration/admin features regression-tested

## Phase 6.3 — Abuse Protection & Rate Limiting

Added local-deployment in-memory abuse controls:

- OTP request rate limit: 5 requests per IP in a 15-minute rolling window.
- OTP verification request limit: 12 requests per IP in a 10-minute rolling window.
- Verification lockout: 15 minutes after the IP-level verification limit is reached.
- Existing per-session 30-second OTP resend cooldown and 5 OTP attempts remain in place.
- IP identification uses `request.remote_addr`; forwarded headers are not trusted.

**Deployment note:** These counters are in-memory and reset when the Flask process restarts. They are suitable for local testing/single-process use; production multi-worker deployment should use a shared store (such as Redis) and carefully configured proxy trust.

**Runtime testing:** Syntax compilation was performed. Runtime/API testing is pending because Flask is not installed in the current editing environment.

## Phase 6.4 — Database Protection & Backup/Restore

- A timestamped SQLite snapshot is created at application startup using SQLite's backup API.
- Admin dashboard includes **Download Database Backup** for an on-demand backup.
- Restore requires an authenticated admin session, CSRF token, explicit `RESTORE` confirmation, and a valid uploaded SQLite backup.
- Restore validation checks `PRAGMA integrity_check` and the expected `visitors` table/columns.
- Before replacing the current database, a pre-restore backup is created. Replacement uses `os.replace` after validation.
- Old backup files are pruned according to the configured retention period.
- Backups are local and are not included in source ZIPs or Git commits.

### Backup retention configuration

Set `VHD_BACKUP_RETENTION_DAYS` in `.env` (default: `30`). The automatic backup and cleanup are best-effort at startup; if backup fails, the app logs the error and continues running.

### Restore instructions

1. Log in to Admin.
2. In **Database Protection**, choose a `.sqlite3`/`.db` backup file.
3. Type `RESTORE` exactly in the confirmation box.
4. Confirm the browser prompt and submit.
5. Restore runs integrity/schema checks and creates a pre-restore snapshot before replacing the database.

**Important:** Keep backup files in a secure location and periodically copy them to a separate protected storage device. A backup on the same computer does not protect against device failure.


## Phase 6.5 — Audit Logging & Security Monitoring

- Stores audit events in the SQLite `audit_logs` table (timestamps, event, category, brief details, actor, and IP address).
- Tracks admin authentication success/failure, logout, visitor registration, visitor details viewing, record deletion, CSV export, backup download, restore, and OTP rate-limit events.
- Admin-only Security Logs page at `/admin/security-logs` with text/category/date filters, pagination, and CSV export.
- Passwords, OTP codes, session tokens, and full visitor form data are intentionally not logged.
- Audit retention defaults to 180 days and can be configured with `VHD_AUDIT_RETENTION_DAYS`; cleanup runs on startup.
- Logs are stored in the same protected SQLite database and included in database backups. This is application-level logging, not tamper-proof external logging.

### Phase 6.5 Test checklist
- [ ] Successful admin login creates `ADMIN_LOGIN_SUCCESS`
- [ ] Incorrect password creates `ADMIN_LOGIN_FAILURE`
- [ ] Admin logout creates `ADMIN_LOGOUT`
- [ ] Successful visitor registration creates `VISITOR_REGISTERED`
- [ ] Viewing details and deleting a visitor create corresponding events
- [ ] CSV export, backup download, and restore create audit events
- [ ] Trigger OTP rate limit and confirm security event is recorded
- [ ] Security Logs page is inaccessible without admin authentication
- [ ] Search, category, date filters, pagination, and CSV export work
- [ ] Confirm no OTP/password/session token/full visitor data appears in log details

## Phase 6.5 — Audit Logging & Security Monitoring

- Audit events stored in SQLite `audit_logs` (timestamp, event, category, brief details, actor, IP address).
- Tracks admin login success/failure, logout, verified visitor registration, visitor details viewed, visitor deletion, visitor CSV export, manual database backup, database restore, and OTP request/verification rate-limit events.
- Admin-only `/admin/security-logs` page supports text search, category filter, date filters, pagination, and CSV export.
- Passwords, OTP codes, session tokens, and full visitor form contents are not written to audit details.
- Audit retention defaults to 180 days and is configurable with `VHD_AUDIT_RETENTION_DAYS`; expired entries are cleaned at startup.
- Logs are stored in the application's SQLite database and included in DB backups. This is application-level logging, not tamper-proof external logging.

### Phase 6.5 test checklist
- [ ] Correct admin login creates `ADMIN_LOGIN_SUCCESS`
- [ ] Wrong admin password creates `ADMIN_LOGIN_FAILURE`
- [ ] Logout creates `ADMIN_LOGOUT`
- [ ] Successful visitor registration creates `VISITOR_REGISTERED`
- [ ] Visitor detail view and deletion create audit events
- [ ] CSV export, manual backup, and restore are logged
- [ ] Trigger OTP rate limits and check security events
- [ ] Security logs page requires admin authentication
- [ ] Search, category, date filters, pagination, and CSV export work
- [ ] Confirm logs never contain passwords, OTPs, session tokens, or full registration data


## Phase 6.6 — Deployment Security & Configuration Hardening

### 6.6.1 Production configuration
- `VHD_ENV=development` is the default for local HTTP testing.
- Set `VHD_ENV=production` only after HTTPS is configured. Production startup requires a strong `VHD_SECRET_KEY` (at least 32 characters) and `VHD_ADMIN_PASSWORD` (at least 12 characters). Debug mode remains disabled.

### 6.6.2 HTTP security headers
The app adds a Content Security Policy, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, and `Cross-Origin-Opener-Policy`. Template inline styles are allowed because existing templates contain inline CSS; JavaScript remains self-hosted only.

### 6.6.3 HTTPS and secure cookies
- In production, session cookies are Secure, HttpOnly, and SameSite=Lax.
- HSTS is emitted only in production. Production must be served through HTTPS.
- Local development on HTTP intentionally keeps Secure cookies off so browser sessions work.

### 6.6.4 Host validation
Only `localhost`, `127.0.0.1`, and `::1` are accepted by default. To use LAN access, add your current machine IP to `VHD_ALLOWED_HOSTS` in `.env` (comma-separated), for example `192.168.29.46`. Do not trust forwarded Host headers unless a reverse proxy is deliberately configured.

### 6.6.5 Safe error handling
404 and 500 responses use generic messages. Detailed failures are written to the server log for troubleshooting, not returned to visitors.

### Local testing
Keep `VHD_ENV=development`, `VHD_COOKIE_SECURE=0`, and add the LAN IP in `VHD_ALLOWED_HOSTS` if testing from a mobile device. Do not set production mode until HTTPS is working.

## Required Visitor Selfie Capture
- The form includes a required **Upload Selfie** control.
- The button opens the native image capture or file picker.
- Mobile browsers supporting capture="user" can open the front camera. Visitors can preview, retake, or remove the photo.
- Registration requires a selfie in both the form and API.
- Selfies are limited to 5 MB, checked as image files, and saved in `private_uploads/selfies` with random filenames (not in public static files).
- Only authenticated admins can view a selfie from its visitor detail page.
- Native file capture can work over HTTP, depending on the device and browser. HTTPS is recommended for protecting visitor data.
- Existing SQLite databases are migrated automatically with a nullable `selfie_filename` column.
