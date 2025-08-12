# app/main.py
import os, base64
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from dotenv import load_dotenv
from urllib.parse import quote
GH_API = "https://api.github.com"

load_dotenv()  # .env 로컬만 사용 (Render에서는 환경변수 직접 설정)

GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")
DEFAULT_REPO = os.getenv("DEFAULT_REPO", "DGM-A-1/SmartFactory_UI")
DEFAULT_REF    = os.getenv("DEFAULT_REF", "develop")
DEFAULT_SUBDIR = os.getenv("DEFAULT_SUBDIR", "")  # 모노레포면 경로 지정, 아니면 빈값

app = FastAPI(title="smartfactory_mcp (REST)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 단계 전체 허용, 운영 시 특정 도메인만
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
async def gh_tree(repo: str, ref: str):
    # 리포의 파일 트리를 가져와서 파일명 검색에 사용
    return await gh_get(f"{GH_API}/repos/{repo}/git/trees/{ref}", params={"recursive": "1"})

async def gh_get(url, params=None):
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent":"smartfactory_mcp/1.0",
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
                 max_results: int = 20, subdir: str | None = None, mode:str = "auto"):
    """
    예: /search?q=main.dart&repo=<id>/smartfactory_ui&ref=main
    """
    repo = repo or DEFAULT_REPO
    ref  = ref or DEFAULT_REF
    subdir = (subdir if subdir is not None else DEFAULT_SUBDIR).strip()
    query = q.strip()

    # --- (A) 파일명 검색: Git tree로 즉시 동작 ---
    is_filename_query = query.startswith("filename:") or any(
        query.endswith(ext) for ext in [".dart", ".yaml", ".yml", ".json", ".md"]
    )
    if mode in ("filename", "auto") and is_filename_query:
        name = query.replace("filename:", "").strip()
        tree = await gh_tree(repo, ref)
        files = [t["path"] for t in tree.get("tree", []) if t.get("type") == "blob"]
        if subdir:
            files = [p for p in files if p.startswith(subdir)]
        hits = [p for p in files if p.split("/")[-1] == name or p.endswith("/" + name)]
        hits = hits[:max_results]
        return [
            {"path": p, "html_url": f"https://github.com/{repo}/blob/{ref}/{p}", "score": 1.0}
            for p in hits
        ]

    # --- (B) 내용 검색(인덱스 기반): GitHub Search API (기본 브랜치 제한) ---
    q2 = query
    if "in:" not in q2 and "filename:" not in q2 and "path:" not in q2:
        q2 = f"{q2} in:file"
    q2 += f" repo:{repo}"
    if subdir:
        q2 += f" path:{subdir}"

    data = await gh_get(f"{GH_API}/search/code", params={"q": q2, "per_page": max_results})
    items = data.get("items", [])
    return [
        {"path": it.get("path"), "html_url": it.get("html_url"), "score": it.get("score")}
        for it in items
    ]
# app/main.py (추가)
@app.get("/tree")
async def tree(ref: str | None = None, subdir: str | None = None, exts: str = ""):
    """
    예) /tree?ref=develop&exts=.dart,.yaml
    """
    repo = DEFAULT_REPO
    ref  = ref or DEFAULT_REF
    subdir = (subdir if subdir is not None else DEFAULT_SUBDIR).strip()
    data = await gh_tree(repo, ref)
    files = [t["path"] for t in data.get("tree", []) if t.get("type") == "blob"]
    if subdir:
        files = [p for p in files if p.startswith(subdir)]
    if exts:
        allow = {e.strip() for e in exts.split(",") if e.strip()}
        files = [p for p in files if any(p.endswith(e) for e in allow)]
    return [{"path": p, "url": f"https://github.com/{repo}/blob/{ref}/{p}"} for p in files]
