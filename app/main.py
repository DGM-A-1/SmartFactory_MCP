# app/main.py
import os, base64
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from dotenv import load_dotenv

load_dotenv()  # .env 로컬만 사용 (Render에서는 환경변수 직접 설정)

GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")
DEFAULT_REPO   = os.getenv("DEFAULT_REPO", "DGM-A-1/smartfactory_ui")  # 깃헙 리포 풀네임
DEFAULT_REF    = os.getenv("DEFAULT_REF", "main")
DEFAULT_SUBDIR = os.getenv("DEFAULT_SUBDIR", "")  # 모노레포면 경로 지정, 아니면 빈값

app = FastAPI(title="smartfactory_mcp (REST)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 단계 전체 허용, 운영 시 특정 도메인만
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def gh_get(url, params=None):
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, headers=headers, params=params)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()

@app.get("/health")
async def health():
    return {"ok": True, "repo": DEFAULT_REPO, "ref": DEFAULT_REF, "subdir": DEFAULT_SUBDIR}

@app.get("/search")
async def search(q: str, repo: str | None = None, ref: str | None = None,
                 max_results: int = 20, subdir: str | None = None):
    """
    예: /search?q=main.dart&repo=<id>/smartfactory_ui&ref=main
    """
    repo = repo or DEFAULT_REPO
    ref  = ref or DEFAULT_REF
    subdir = (subdir if subdir is not None else DEFAULT_SUBDIR).strip()

    query = f"{q} repo:{repo} in:file"
    if subdir:
        query += f" path:{subdir}"

    data = await gh_get("https://api.github.com/search/code",
                        params={"q": query, "per_page": max_results})
    items = data.get("items", [])
    return [
        {"path": it.get("path"), "html_url": it.get("html_url"), "score": it.get("score")}
        for it in items
    ]

@app.get("/fetch")
async def fetch(path: str, repo: str | None = None, ref: str | None = None):
    """
    예: /fetch?path=lib/main.dart&repo=<id>/smartfactory_ui&ref=main
    """
    repo = repo or DEFAULT_REPO
    ref  = ref or DEFAULT_REF
    url = f"https://api.github.com/repos/{repo}/contents/{path}"

    data = await gh_get(url, params={"ref": ref})
    content = data.get("content", "")
    encoding = data.get("encoding", "")

    text = ""
    if encoding == "base64" and content:
        try:
            text = base64.b64decode(content).decode("utf-8", errors="ignore")
        except Exception:
            text = ""
    return {"path": path, "ref": ref, "text": text}
