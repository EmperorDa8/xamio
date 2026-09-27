"""Load / stress test against a running Xamio API.

Run the API the way Render does (single uvicorn worker, proxy headers on, auth
enforced) and point this at it:

    SUPABASE_JWT_SECRET=... TASK_SECRET=... uvicorn main:app --port 8765 \
        --proxy-headers --forwarded-allow-ips=*
    python tests/load/stress.py http://127.0.0.1:8765

Each simulated student gets its own X-Forwarded-For address, so the per-IP rate
limiter sees many users rather than one noisy one — as it does in production.
"""
import asyncio
import io
import os
import random
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import samples  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
SECRET = os.environ["SUPABASE_JWT_SECRET"]
TASK = os.environ["TASK_SECRET"]


def token(i: int) -> str:
    return jwt.encode(
        {"sub": f"load-{i}", "email": f"load{i}@uni.edu", "aud": "authenticated", "exp": int(time.time()) + 3600},
        SECRET,
        algorithm="HS256",
    )


def headers(i: int) -> dict:
    return {"Authorization": f"Bearer {token(i)}", "X-Forwarded-For": f"10.{i // 65536 % 256}.{i // 256 % 256}.{i % 256}"}


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))]


def report(name, lat, codes, wall):
    ok = sum(1 for c in codes if 200 <= c < 300)
    by = {}
    for c in codes:
        by[c] = by.get(c, 0) + 1
    print(
        f"{name:<34} n={len(codes):<5} ok={ok:<5} codes={by} "
        f"p50={pct(lat, .5)*1000:7.0f}ms p95={pct(lat, .95)*1000:7.0f}ms "
        f"max={max(lat or [0])*1000:7.0f}ms  {len(codes)/wall:6.1f} req/s"
    )
    return {"ok": ok, "n": len(codes), "p95": pct(lat, .95), "max": max(lat or [0]), "codes": by}


async def burst(client, n, concurrency, make_request):
    sem = asyncio.Semaphore(concurrency)
    lat, codes = [], []

    async def one(i):
        async with sem:
            t = time.perf_counter()
            try:
                r = await make_request(client, i)
                codes.append(r.status_code)
            except Exception as e:
                codes.append(type(e).__name__)
            lat.append(time.perf_counter() - t)

    t0 = time.perf_counter()
    await asyncio.gather(*(one(i) for i in range(n)))
    return lat, codes, time.perf_counter() - t0


def exams(n):
    base = date.today()
    return [
        {
            "course_code": f"C{i:03d}",
            "course_name": "Course",
            "date": (base + timedelta(days=5 + i % 40)).isoformat(),
            "time": ["09:00", "13:00"][i % 2],
            "duration_minutes": 120,
            "venue": "Hall",
        }
        for i in range(n)
    ]


def heavy_pdf(pages=120) -> bytes:
    """A long text PDF — slow to parse, like a real 100-page faculty timetable."""
    one = samples.as_pdf()
    # Reuse the one-page builder: stitch many copies of its page content.
    from pypdf import PdfReader, PdfWriter  # optional; fall back below

    reader = PdfReader(io.BytesIO(one))
    w = PdfWriter()
    for _ in range(pages):
        w.add_page(reader.pages[0])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


async def main():
    results = {}
    limits = httpx.Limits(max_connections=400, max_keepalive_connections=100)
    async with httpx.AsyncClient(base_url=BASE, timeout=180, limits=limits) as c:
        # Warm-up
        await c.get("/health")

        # 1. Raw capacity on the cheapest endpoint.
        lat, codes, wall = await burst(c, 1000, 100, lambda c, i: c.get("/health"))
        results["health"] = report("GET /health x1000 @100", lat, codes, wall)

        # 2. Calendar export: realistic 20-exam schedules from 300 different students.
        body = {"exams": exams(20), "timezone": "Africa/Lagos"}
        lat, codes, wall = await burst(
            c, 300, 50, lambda c, i: c.post("/download/ics", json=body, headers=headers(i))
        )
        results["ics"] = report("POST /download/ics x300 @50", lat, codes, wall)

        # 3. Upload burst: 100 students parse a timetable at once.
        txt = samples.as_txt()
        lat, codes, wall = await burst(
            c,
            100,
            25,
            lambda c, i: c.post(
                "/parse",
                files={"file": ("t.txt", txt)},
                data={"courses_text": "CSC201, MTH211, CSC209"},
                headers=headers(1000 + i),
            ),
        )
        results["parse"] = report("POST /parse (txt) x100 @25", lat, codes, wall)

        # 4. Rate limit: one student hammering /parse gets throttled, not served.
        lat, codes, wall = await burst(
            c,
            20,
            5,
            lambda c, i: c.post(
                "/parse", files={"file": ("t.txt", txt)}, data={"courses_text": "CSC201"}, headers=headers(5000)
            ),
        )
        results["ratelimit"] = report("POST /parse same-IP x20", lat, codes, wall)

        # 5. Head-of-line blocking: while a few students upload a LONG pdf, does
        #    the API still answer everyone else?
        try:
            pdf = heavy_pdf()
        except ImportError:
            pdf = None
        if pdf:
            health_lat = []
            stop = asyncio.Event()

            async def prober():
                while not stop.is_set():
                    t = time.perf_counter()
                    await c.get("/health")
                    health_lat.append(time.perf_counter() - t)
                    await asyncio.sleep(0.1)

            probe = asyncio.create_task(prober())
            t0 = time.perf_counter()
            heavy = await asyncio.gather(
                *(
                    c.post(
                        "/parse",
                        files={"file": ("long.pdf", pdf)},
                        data={"courses_text": "CSC201, MTH211"},
                        headers=headers(7000 + i),
                    )
                    for i in range(4)
                )
            )
            heavy_wall = time.perf_counter() - t0
            stop.set()
            await probe
            print(
                f"{'4x heavy PDF parse (120 pages)':<34} codes={[r.status_code for r in heavy]} wall={heavy_wall:.1f}s | "
                f"/health during it: p50={pct(health_lat,.5)*1000:.0f}ms max={max(health_lat)*1000:.0f}ms"
            )
            results["blocking"] = {"health_max": max(health_lat), "heavy_wall": heavy_wall}

        # 6. Reminder dispatch under contention: 10 overlapping runs.
        lat, codes, wall = await burst(
            c, 10, 10, lambda c, i: c.post("/tasks/dispatch-reminders", headers={"X-Task-Key": TASK})
        )
        results["dispatch"] = report("POST dispatch x10 overlapping", lat, codes, wall)

    return results


if __name__ == "__main__":
    asyncio.run(main())
