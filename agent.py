import os
import json
from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from logger import logger

# Skema output terstruktur dari AI
class InlineComment(BaseModel):
    file_path: str = Field(description="Path file relatif terhadap root repository, misal: lib/main.dart atau src/auth.py")
    line_number: int = Field(description="Nomor baris baru (new line) pada perubahan kode tempat masalah ditemukan")
    category: Literal["SECURITY", "BUG", "PERFORMANCE", "QUALITY"] = Field(
        description="Kategori temuan: SECURITY (celah keamanan/secret), BUG (logika keliru/null crash/race condition), PERFORMANCE (leak/N+1/slow query), QUALITY (arsitektur/maintainability)"
    )
    severity: Literal["CRITICAL", "WARNING", "SUGGESTION"] = Field(
        description="Tingkat keparahan: CRITICAL (blocker/vulnerability), WARNING (potensi bug/unhandled error), SUGGESTION (peningkatan kode)"
    )
    comment: str = Field(
        description="Penjelasan masalah secara ringkas, sopan, dan jelas beserta alasan mengapa kode saat ini bermasalah"
    )
    suggested_replacement: Optional[str] = Field(
        default=None,
        description="Potongan kode pengganti baris tersebut (opsional, jika ingin memberikan tombol 1-click commit suggestion)"
    )

class ReviewReport(BaseModel):
    summary: str = Field(description="Ringkasan evaluasi PR secara umum (2-4 kalimat)")
    verdict: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"] = Field(
        description="Rekomendasi status: APPROVE jika aman, REQUEST_CHANGES jika ada isu CRITICAL, COMMENT jika ada catatan/saran minor"
    )
    inline_comments: List[InlineComment] = Field(
        default_factory=list,
        description="Daftar temuan spesifik per baris kode yang perlu diberi komentar langsung di PR"
    )

SYSTEM_INSTRUCTION = """
Kamu adalah Principal Software Engineer & Lead Security Architect.
Tugasmu mereview Pull Request secara objektif, mendalam, dan konstruktif.

Panduan Review:
1. PRIORITAS UTAMA:
   - Keamanan (Secret leakage, SQL injection, XSS, SSRF, broken access control).
   - Potensi Bug (Null safety violation, unhandled async exception, race condition, index out of bounds).
   - Performa (Memory leak, stream unclosed, N+1 database queries, loop yang tidak efisien).
2. JANGAN NITPICKING:
   - Abaikan masalah format spasi, tanda kurung, atau style formatting yang sudah ditangani linter otomatis.
   - Jangan mengomentari kode yang sudah baik hanya demi menghasilkan komentar.
3. AKURASI BARIS KODE:
   - Pastikan 'line_number' adalah baris baru (tanda '+' pada diff) tempat masalah berada.
4. SOLUSI PRAKTIS:
   - Jika memungkinkan, berikan 'suggested_replacement' yang bisa langsung diterapkan oleh author PR.
"""

def _call_openrouter(api_key: str, model_name: str, user_prompt: str) -> ReviewReport:
    """Memanggil model melalui OpenRouter API (OpenAI-compatible)."""
    from openai import OpenAI
    
    logger.info(f"[OpenRouter] Mengirim diff ke model '{model_name}'...")
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        default_headers={
            "HTTP-Referer": "https://github.com",
            "X-Title": "GitHub AI Code Reviewer"
        }
    )

    schema_json = json.dumps(ReviewReport.model_json_schema(), indent=2)
    full_system = f"{SYSTEM_INSTRUCTION}\n\nWAJIB menjawab HANYA dalam format JSON valid yang mengikuti skema Pydantic berikut:\n{schema_json}"

    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": full_system},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    raw_text = response.choices[0].message.content
    return ReviewReport.model_validate_json(raw_text)

def _call_gemini(api_key: str, model_name: str, user_prompt: str) -> ReviewReport:
    """Memanggil model native Google Gemini SDK."""
    from google import genai
    from google.genai import types

    logger.info(f"[Gemini] Mengirim diff ke model '{model_name}'...")
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=model_name,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=ReviewReport,
            temperature=0.2,
        ),
    )

    return ReviewReport.model_validate_json(response.text)

def analyze_diff_with_ai(pr_title: str, pr_body: str, combined_diff: str) -> ReviewReport:
    """Mengirim diff kode ke AI Model (OpenRouter atau Gemini) dan menghasilkan laporan review terstruktur."""
    provider = os.getenv("AI_PROVIDER", "").lower()
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")

    user_prompt = f"""
    Pull Request Title: {pr_title}
    Pull Request Description: {pr_body or 'Tidak ada deskripsi'}

    Berikut adalah Git Patch/Diff dari file-file yang berubah:
    ```diff
    {combined_diff[:40000]}
    ```

    Lakukan evaluasi kode secara menyeluruh dan kembalikan hasil dalam format JSON terstruktur.
    """

    # 1. Gunakan OpenRouter jika disetel atau jika OPENROUTER_API_KEY tersedia
    if provider == "openrouter" or (openrouter_key and "MASUKKAN_" not in openrouter_key):
        if not openrouter_key or "MASUKKAN_" in openrouter_key:
            raise ValueError("OPENROUTER_API_KEY belum diisi di file .env")
        
        # Default model OpenRouter jika tidak dispesifikasikan
        model_name = os.getenv("AI_MODEL", "anthropic/claude-3.5-sonnet")
        return _call_openrouter(openrouter_key, model_name, user_prompt)

    # 2. Fallback ke Gemini jika GEMINI_API_KEY tersedia
    elif gemini_key and "MASUKKAN_" not in gemini_key:
        model_name = os.getenv("AI_MODEL", "gemini-2.5-pro")
        return _call_gemini(gemini_key, model_name, user_prompt)

    else:
        logger.error("Tidak ada API Key valid (OPENROUTER_API_KEY atau GEMINI_API_KEY) yang dikonfigurasi.")
        raise ValueError("Silakan isi OPENROUTER_API_KEY atau GEMINI_API_KEY di file .env")
