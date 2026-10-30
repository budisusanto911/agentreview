# Agent Review

Automated Code Review AI Agent menggunakan GitHub Webhook mandiri dan LLM (OpenRouter / Gemini).

## Fitur
- **Pure Webhook**: Berjalan tanpa ketergantungan GitHub Actions.
- **Dukungan Multi-Model**: Mendukung Claude 3.5 Sonnet, DeepSeek, Qwen via OpenRouter serta Google Gemini.
- **Komentar Inline PR**: Komentar otomatis langsung di baris kode dengan tombol *1-click commit suggestion*.
- **Live Logs Dashboard**: Antarmuka web modern untuk memantau aktivitas server dan proses review secara real-time.
- **Keamanan**: Validasi HMAC SHA-256 signature, anti-loop, debounce, dan filter draft PR.

## Menjalankan Server

1. Salin file environment:
   ```bash
   cp .env.example .env
   ```
2. Isi `GITHUB_TOKEN`, `OPENROUTER_API_KEY` atau `GEMINI_API_KEY` di file `.env`.
3. Pasang dependensi:
   ```bash
   pip install -r requirements.txt
   ```
4. Jalankan server:
   ```bash
   ./start_server.sh
   ```
5. Buka dashboard di `http://localhost:8000/dashboard`.
