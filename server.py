import os
import hmac
import hashlib
import time
import json
import urllib.parse
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from github import Github, GithubException

from logger import logger
from agent import analyze_diff_with_ai, ReviewReport

# Muat environment variables dari .env
load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

if not GITHUB_TOKEN or "MASUKKAN_" in GITHUB_TOKEN:
    logger.warning("GITHUB_TOKEN belum dikonfigurasi dengan benar di file .env")

app = FastAPI(
    title="GitHub AI Code Review Webhook Server",
    description="Webhook server mandiri untuk automated code review & direct PR comments",
    version="1.0.0"
)

# Set untuk mencegah duplikasi review bersamaan pada PR yang sama (Debounce)
ACTIVE_REVIEWS = set()

# Ekstensi file yang diabaikan saat mereview
IGNORED_EXTENSIONS = (
    '.lock', '.png', '.jpg', '.jpeg', '.svg', '.gif', '.ico',
    '.pdf', '.zip', '.tar', '.gz', '.min.js', '.min.css',
    '.map', '.json', '.g.dart', '.freezed.dart'
)

# Middleware untuk mencatat log setiap request HTTP masuk
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    client_ip = request.client.host if request.client else "unknown"
    method = request.method
    path = request.url.path

    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000

    logger.info(f"HTTP {method} {path} | Status: {response.status_code} | Client: {client_ip} | Waktu: {duration_ms:.2f}ms")
    return response

def verify_github_signature(payload_bytes: bytes, signature_header: Optional[str]):
    """Memvalidasi HMAC-SHA256 signature dari GitHub Webhook."""
    if not WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET kosong. Melewati verifikasi signature.")
        return

    if not signature_header:
        logger.error("Ditolak: Header X-Hub-Signature-256 tidak disertakan oleh client.")
        raise HTTPException(status_code=403, detail="Header X-Hub-Signature-256 tidak ditemukan.")

    parts = signature_header.split("=")
    if len(parts) != 2 or parts[0] != "sha256":
        logger.error(f"Ditolak: Format header signature tidak valid: {signature_header}")
        raise HTTPException(status_code=400, detail="Format header signature tidak valid.")

    secret_bytes = WEBHOOK_SECRET.encode("utf-8")
    mac = hmac.new(secret_bytes, msg=payload_bytes, digestmod=hashlib.sha256)
    expected_signature = mac.hexdigest()

    if not hmac.compare_digest(expected_signature, parts[1]):
        logger.error("Ditolak: HMAC Signature tidak cocok! Potensi request palsu.")
        raise HTTPException(status_code=403, detail="Signature tidak cocok! Request ditolak.")

    logger.info("Verifikasi HMAC Signature Webhook: BERHASIL (Valid)")

def run_pr_review_task(repo_full_name: str, pr_number: int):
    """Proses review AI di background dan kirim komentar langsung ke GitHub."""
    pr_key = f"{repo_full_name}#{pr_number}"
    if pr_key in ACTIVE_REVIEWS:
        logger.warning(f"Debounce: Review untuk {pr_key} sedang aktif berjalan. Mencegah duplikasi task.")
        return

    ACTIVE_REVIEWS.add(pr_key)
    logger.info(f"Memulai pipeline AI Review untuk repo: '{repo_full_name}', PR: #{pr_number}")
    gh = Github(GITHUB_TOKEN)

    try:
        repo = gh.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)

        # Abaikan jika PR berstatus Draft
        if pr.draft:
            logger.info(f"PR #{pr_number} berstatus 'Draft'. Review dilewati.")
            return

        # 1. Kumpulkan diff kode yang diubah
        diff_builder = []
        file_count = 0
        for f in pr.get_files():
            if f.filename.endswith(IGNORED_EXTENSIONS):
                logger.debug(f"Mengabaikan file non-code / generated: {f.filename}")
                continue
            if f.patch:
                file_count += 1
                diff_builder.append(f"### File: {f.filename} (Status: {f.status})\n{f.patch}\n")

        combined_diff = "\n".join(diff_builder)
        if not combined_diff.strip():
            logger.info(f"Tidak ada perubahan kode yang perlu direview pada PR #{pr_number}.")
            return

        logger.info(f"Ditemukan {file_count} file yang dimodifikasi ({len(combined_diff)} karakter diff). Mengirim ke AI...")

        # 2. Panggil AI Agent untuk evaluasi
        report: ReviewReport = analyze_diff_with_ai(
            pr_title=pr.title,
            pr_body=pr.body or "",
            combined_diff=combined_diff
        )

        logger.info(f"AI Analisis selesai! Verdict: {report.verdict} | Total Komentar Inline: {len(report.inline_comments)}")

        # 3. Format komentar ringkasan utama
        verdict_badge = {
            "APPROVE": "✅ **SIAP DIMERGE (Approved)**",
            "REQUEST_CHANGES": "🛑 **PERLU PERBAIKAN SEBELUM MERGE (Changes Requested)**",
            "COMMENT": "💬 **CATATAN REVIEW (Advisory Comments)**"
        }.get(report.verdict, "💬 **CATATAN REVIEW**")

        summary_body = f"""### 🤖 AI Code Review Summary

{verdict_badge}

{report.summary}

---
"""
        # 4. Ambil commit terbaru dari PR
        latest_commit = list(pr.get_commits())[-1]

        # 5. Siapkan komentar inline per baris kode
        inline_comments_payload = []
        fallback_notes = []

        for item in report.inline_comments:
            icon = "🔴" if item.severity == "CRITICAL" else ("🟡" if item.severity == "WARNING" else "💡")
            comment_text = f"{icon} **[{item.severity}] {item.category}**\n\n{item.comment}"
            if item.suggested_replacement:
                comment_text += f"\n\n```suggestion\n{item.suggested_replacement}\n```"

            inline_comments_payload.append({
                "path": item.file_path,
                "line": item.line_number,
                "side": "RIGHT",
                "body": comment_text
            })
            fallback_notes.append(f"- **`{item.file_path}:{item.line_number}`** [{item.category}]: {item.comment}")

        # 6. Kirim ke GitHub API
        try:
            if inline_comments_payload:
                logger.info(f"Memposting batch review ({len(inline_comments_payload)} inline comments) ke GitHub...")
                pr.create_review(
                    commit=latest_commit,
                    body=summary_body,
                    event="COMMENT",
                    comments=inline_comments_payload
                )
            else:
                logger.info("Memposting general review summary ke GitHub...")
                pr.create_review(commit=latest_commit, body=summary_body, event="COMMENT")

            logger.info(f"SUKSES: Review dan komentar berhasil diposting di {repo_full_name} PR #{pr_number}")

        except GithubException as ghe:
            logger.warning(f"Batch inline comment gagal ({ghe.data.get('message', ghe)}). Mengalihkan ke general comment fallback...")
            if fallback_notes:
                summary_body += "\n#### Temuan Detail:\n" + "\n".join(fallback_notes)
            pr.create_issue_comment(summary_body)
            logger.info(f"SUKSES (Fallback): Komentar berhasil diposting di {repo_full_name} PR #{pr_number}")

    except Exception as e:
        logger.error(f"Gagal memproses review PR #{pr_number}: {e}", exc_info=True)
    finally:
        ACTIVE_REVIEWS.discard(pr_key)

@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard():
    """Menampilkan dashboard HTML live logs dan monitoring server."""
    html_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "dashboard.html")
    if os.path.exists(html_file):
        with open(html_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Template dashboard.html tidak ditemukan.</h1>"

@app.get("/")
def health_check():
    has_github = bool(GITHUB_TOKEN and "MASUKKAN_" not in GITHUB_TOKEN)
    has_openrouter = bool(os.getenv("OPENROUTER_API_KEY") and "MASUKKAN_" not in os.getenv("OPENROUTER_API_KEY"))
    has_gemini = bool(os.getenv("GEMINI_API_KEY") and "MASUKKAN_" not in os.getenv("GEMINI_API_KEY"))
    has_ai = has_openrouter or has_gemini
    active_provider = "openrouter" if has_openrouter else ("gemini" if has_gemini else "none")

    return {
        "status": "online",
        "service": "GitHub AI Code Review Webhook Server",
        "ready": bool(has_github and has_ai),
        "configured": {
            "github_token": has_github,
            "openrouter_api_key": has_openrouter,
            "gemini_api_key": has_gemini,
            "active_provider": active_provider,
            "model": os.getenv("AI_MODEL", "anthropic/claude-3.5-sonnet"),
            "webhook_secret": bool(WEBHOOK_SECRET)
        }
    }

@app.get("/logs")
def view_recent_logs(lines: int = 50):
    """Endpoint untuk melihat log terbaru secara live dari browser."""
    log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "server.log")
    if not os.path.exists(log_file_path):
        return {"logs": ["Log file belum dibuat."]}
    
    with open(log_file_path, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
        recent_lines = all_lines[-lines:]
        return {"total_lines": len(all_lines), "recent_logs": [line.rstrip() for line in recent_lines]}

@app.post("/webhook")
async def handle_github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: Optional[str] = Header(None),
    x_hub_signature_256: Optional[str] = Header(None)
):
    body_bytes = await request.body()
    verify_github_signature(body_bytes, x_hub_signature_256)

    # Dukung baik 'application/json' maupun 'application/x-www-form-urlencoded'
    content_type = request.headers.get("content-type", "")
    try:
        if "application/json" in content_type:
            payload = json.loads(body_bytes.decode("utf-8"))
        elif "application/x-www-form-urlencoded" in content_type:
            parsed = urllib.parse.parse_qs(body_bytes.decode("utf-8"))
            payload_str = parsed.get("payload", ["{}"])[0]
            payload = json.loads(payload_str)
        else:
            # Fallback jika header tidak standar
            payload = json.loads(body_bytes.decode("utf-8"))
    except Exception as parse_err:
        logger.error(f"Gagal mem-parsing payload webhook: {parse_err}")
        return {"status": "error", "message": f"Invalid payload format: {parse_err}"}

    # Skenario 0: Event 'ping' dari GitHub saat webhook baru pertama kali ditambahkan
    if x_github_event == "ping":
        zen = payload.get("zen", "Non-blocking is better than blocking.")
        hook_id = payload.get("hook_id", "unknown")
        logger.info(f"Ping dari GitHub diterima (Hook ID: {hook_id}) - Zen: '{zen}'. Webhook siap!")
        return {
            "status": "pong",
            "message": "GitHub Webhook Connected Successfully!",
            "hook_id": hook_id
        }

    # Cegah loop jika event dipicu oleh bot itu sendiri
    sender_type = payload.get("sender", {}).get("type", "")
    if sender_type.lower() == "bot":
        logger.info("Event dari bot diabaikan untuk mencegah loop.")
        return {"status": "ignored", "reason": "Event dari bot diabaikan untuk mencegah loop."}

    # Skenario 1: Event Pull Request (dibuka atau ada commit baru yang di-push)
    if x_github_event == "pull_request":
        action = payload.get("action")
        pr_number = payload["pull_request"]["number"]
        repo_full_name = payload["repository"]["full_name"]

        if action in ["opened", "synchronize", "reopened"]:
            logger.info(f"Menerima Webhook Event: 'pull_request' ({action}) pada {repo_full_name} PR #{pr_number}")
            background_tasks.add_task(run_pr_review_task, repo_full_name, pr_number)
            return {
                "status": "queued",
                "message": f"AI review dijadwalkan untuk {repo_full_name} PR #{pr_number} (Action: {action})"
            }
        else:
            logger.info(f"Event pull_request action '{action}' diabaikan.")

    # Skenario 2: Trigger manual via komentar PR (misal user mengetik '/review')
    elif x_github_event == "issue_comment":
        action = payload.get("action")
        comment_body = payload.get("comment", {}).get("body", "").strip().lower()
        is_pr = "pull_request" in payload.get("issue", {})

        if action == "created" and is_pr and "/review" in comment_body:
            pr_number = payload["issue"]["number"]
            repo_full_name = payload["repository"]["full_name"]
            logger.info(f"Menerima Webhook Event: 'issue_comment' (/review) pada {repo_full_name} PR #{pr_number}")
            background_tasks.add_task(run_pr_review_task, repo_full_name, pr_number)
            return {
                "status": "queued",
                "message": f"Manual trigger /review diterima untuk PR #{pr_number}"
            }

    # Skenario 3: Event push biasa
    elif x_github_event == "push":
        logger.info("Menerima event 'push'. Catatan: AI code review aktif saat ada Pull Request (PR).")
        return {"status": "ok", "message": "Push event received. AI reviewer listens on pull_request."}

    return {"status": "ignored", "event": x_github_event}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Memulai server uvicorn pada port {port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
