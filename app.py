"""Flask entry point, also discovered by Vercel."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from beeguard_web import create_app

app = create_app()

if __name__ == "__main__":
    from waitress import serve
    print("BeeGuard: http://127.0.0.1:5000", flush=True)
    serve(app, host="127.0.0.1", port=5000, threads=4)
