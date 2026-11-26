# Local deployment notes

For Vercel, follow [VERCEL_DEPLOYMENT.md](VERCEL_DEPLOYMENT.md). The older Waitress/SQLite instructions below apply only to local development and are not Vercel production setup.

# Deployment and security handoff
Status: hardened deployment candidate, not certified or live-tested. The target assumed is a single Windows host with persistent local storage, Waitress and an HTTPS reverse proxy. Do not expose Flask's development server publicly.

1. Back up your existing .env, visitor_records.db, backups and private_uploads directory before replacing files. Stop the running application. Keep the database and selfie directory together. The supplied ZIP contains no live records.
2. Use Python 3.12 or 3.13. Create a virtual environment (`py -m venv .venv`); activate it in PowerShell (`.\.venv\Scripts\Activate.ps1`), then `python -m pip install -r requirements.txt`.
3. Copy .env.example to .env, generate a unique secret with `python -c "import secrets; print(secrets.token_hex(32))"`, set a unique long admin password, set VHD_ENV=production, set VHD_ALLOWED_HOSTS to the actual HTTPS domain. Use VHD_DATA_DIR for an absolute private persistent directory if desired; otherwise existing project-local paths are retained. Protect the directory with Windows NTFS permissions and encrypted storage. Never commit .env.
4. Run `waitress-serve --listen=127.0.0.1:5000 --threads=4 app:app`. Configure an HTTPS reverse proxy such as Caddy using the example and real DNS. For a private LAN, install a trusted internal CA/certificate on each client or use a real trusted HTTPS hostname. Keep port 5000 inaccessible from outside the host. Configure proxy rate limiting and admin access restrictions; application IP limiting sees the proxy address and is shared across clients. Do not blindly trust forwarded headers. Run a single application process.
5. Local testing only: VHD_ENV=development and `python app.py`; open http://localhost:5000 for desktop webcam. Mobile capture is browser/OS dependent; HTTPS required for reliable desktop webcam from other devices. Native capture is not proof of liveness and may offer gallery selection.
6. Before public launch run `python -m unittest discover -s tests -v`, install/run pip-audit against the environment, test mobile and Windows camera permissions, HTTPS, admin login/logout, CSV, restore and disaster recovery. No dependency vulnerability feed or runtime browser test was available during this audit.
7. Backups: the existing SQLite backup only includes database rows, not image files. Schedule encrypted backups of both the database (consistent snapshot) and private_uploads, test recovery, and set a visitor/selfie retention policy. Deletion currently retains selfie files for backup recovery; securely purge according to that policy. Online database restore is disabled in production to prevent concurrent database replacement; stop the server and restore a trusted matching database and image backup offline. Never restore untrusted SQLite files.
8. OTP is NOT implemented in this source: registrations store mobile_verified=0. Do not represent numbers as verified. No new SMS service has been added. Restrict access to reception staff; obtain visitor consent and set retention requirements. Public exposure requires a staging security test, dependency scan, rate-limit review and privacy review.

## Source audit changes
- Desktop getUserMedia and mobile native capture routing; CSP permits blob previews.
- Mandatory image enforced by server; full image decode, pixel and size limit, JPEG re-encoding strips metadata and arbitrary trailing bytes; randomized filenames and protected admin retrieval.
- Production rejects example credentials; secure session cookies and existing CSRF checks retained; malformed Unicode tokens handled.
- Rate-limit maps synchronized and bounded, image processing occurs after rate limit; limits are process-local and reset on restart.
- CSV formula injection mitigation; SQLite trusted_schema disabled, explicit connection closure; production online restore disabled.
- Persistent data location configurable; production development-server execution blocked.
- Existing parameterized SQL and Jinja autoescaping retained. No finding is a guarantee of absence of vulnerabilities. Session cookies are signed client-side and not centrally revocable; add server-side sessions/MFA before higher-risk Internet deployments. Existing images are not retroactively sanitized.
