# app/main.py
import os, base64
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse  # ✅ 추가
import httpx
from dotenv import load_dotenv
from urllib.parse import quote

GH_API = "https://api.github.com"
load_dotenv()

GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")
DEFAULT_REPO   = os.getenv("DEFAULT_REPO", "DGM-A-1/SmartFactory_UI")
DEFAULT_REF    = os.getenv("DEFAULT_REF", "develop")
DEFAULT_SUBDIR = os.getenv("DEFAULT_SUBDIR", "")

app = FastAPI(title="smartfactory_mcp (REST)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _headers():
    h = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "smartfactory_mcp/1.0",
    }
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h

async def gh_get(url, params=None):
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, headers=_headers(), params=params)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()

async def resolve_ref_sha(repo: str, ref: str) -> str:
    """
    브랜치/태그명을 커밋 SHA로 해석. 실패하면 ref를 그대로 반환(이미 SHA일 수 있음).
    """
    # heads (브랜치) 우선
    try:
        data = await gh_get(f"{GH_API}/repos/{repo}/git/refs/heads/{ref}")
        return data["object"]["sha"]
    except HTTPException:
        pass
    # tags(태그) 시도
    try:
        data = await gh_get(f"{GH_API}/repos/{repo}/git/refs/tags/{ref}")
        return data["object"]["sha"]
    except HTTPException:
        pass
    # 마지막으로 그냥 ref를 SHA로 취급
    return ref

async def gh_tree(repo: str, ref: str):
    sha = await resolve_ref_sha(repo, ref)
    return await gh_get(f"{GH_API}/repos/{repo}/git/trees/{sha}", params={"recursive": "1"})

@app.get("/health")
async def health():
    return {"ok": True, "repo": DEFAULT_REPO, "ref": DEFAULT_REF, "subdir": DEFAULT_SUBDIR}

@app.get("/search")
async def search(q: str, repo: str | None = None, ref: str | None = None,
                 max_results: int = 20, subdir: str | None = None, mode: str = "auto"):
    """
    예: /search?q=main.dart&repo=<owner/repo>&ref=develop
    - filename 검색: 'filename:main.dart' 또는 확장자로 자동 감지
    - 내용 검색: GitHub code search (기본 브랜치 인덱스 기준, ref 지정 불가)
    """
    repo = repo or DEFAULT_REPO
    ref  = ref or DEFAULT_REF
    subdir = (subdir if subdir is not None else DEFAULT_SUBDIR).strip()
    query = q.strip()

    # (A) 파일명 검색
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

    # (B) 내용 검색 (기본 브랜치 인덱스 한정)
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

# ✅ 추가: 파일 원문 가져오기
@app.get("/fetch", response_class=PlainTextResponse)
async def fetch(path: str, repo: str | None = None, ref: str | None = None):
    """
    예) /fetch?path=pubspec.yaml&ref=develop
        /fetch?path=lib/main.dart&ref=develop
    private repo도 지원(PAT 필요). 텍스트 파일 기준.
    """
    repo = repo or DEFAULT_REPO
    ref  = ref or DEFAULT_REF

    # 경로 정리(보안)
    path = path.lstrip("/").replace("\\", "/")
    if ".." in path:
        raise HTTPException(400, "invalid path")

    url = f"{GH_API}/repos/{repo}/contents/{quote(path)}"
    data = await gh_get(url, params={"ref": ref})
    if isinstance(data, dict) and data.get("encoding") == "base64" and "content" in data:
        try:
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception:
            # 바이너리/인코딩 이슈 시 짧은 메시지 반환
            raise HTTPException(415, "unsupported or binary content")
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")

    raise HTTPException(404, "file not found or unsupported")
