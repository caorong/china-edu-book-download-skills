#!/usr/bin/env python3
"""Search and download Chinese K-12 textbooks from SmartEdu.

The command uses the public textbook metadata feeds published by the National
Smart Education Platform for Primary and Secondary Schools.  PDF downloads are
restricted to official SmartEdu/CDN hosts and, when a resource requires login,
reuse credentials supplied by the user.

Python 3.10+; standard library only.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import hashlib
import http.cookiejar
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

__version__ = "1.0.0"

VERSION_URL = (
    "https://s-file-2.ykt.cbern.com.cn/"
    "zxx/ndrs/resources/tch_material/version/data_version.json"
)
DETAIL_URLS = (
    "https://s-file-1.ykt.cbern.com.cn/"
    "zxx/ndrv2/resources/tch_material/details/{resource_id}.json",
    "https://s-file-2.ykt.cbern.com.cn/"
    "zxx/ndrv2/resources/tch_material/details/{resource_id}.json",
)
DETAIL_PAGE = (
    "https://basic.smartedu.cn/tchMaterial/detail?"
    "contentType=assets_document&contentId={resource_id}"
    "&catalogType=tchMaterial&subCatalog=tchMaterial"
)
LEGACY_PDF = (
    "https://r1-ndr.ykt.cbern.com.cn/edu_product/esp/"
    "assets_document/{resource_id}.pkg/pdf.pdf"
)
NDR_PRIVATE_HOSTS = (
    "r1-ndr-private.ykt.cbern.com.cn",
    "r2-ndr-private.ykt.cbern.com.cn",
    "r3-ndr-private.ykt.cbern.com.cn",
)
NDR_PUBLIC_HOSTS = (
    "r1-ndr.ykt.cbern.com.cn",
    "r2-ndr.ykt.cbern.com.cn",
    "r3-ndr.ykt.cbern.com.cn",
)
ALLOWED_HOST_SUFFIXES = (
    ".smartedu.cn",
    ".zxx.edu.cn",
    ".ykt.cbern.com.cn",
    ".eduyun.cn",
)
ALLOWED_EXACT_HOSTS = {
    "smartedu.cn",
    "zxx.edu.cn",
    "basic.smartedu.cn",
    "www.zxx.edu.cn",
}
RESOURCE_ID_RE = re.compile(
    r"(?i)(?<![0-9a-f])"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"(?![0-9a-f])"
)
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
TAG_DIMENSIONS = {
    "zxxxd": "stage",
    "zxxxk": "subject",
    "zxxbb": "version",
    "zxxnj": "grade",
    "zxxcc": "volume",
    "5036342742": "category",
    "tagView": "tag_view",
}
VERSION_ALIASES = {
    "人教版": ("人教版", "人民教育出版社"),
    "北师大版": ("北师大版", "北京师范大学出版社"),
    "苏教版": ("苏教版", "江苏凤凰教育出版社", "江苏教育出版社"),
    "沪教版": ("沪教版", "上海教育出版社"),
    "冀教版": ("冀教版", "河北教育出版社"),
    "外研版": ("外研版", "外语教学与研究出版社"),
    "教科版": ("教科版", "教育科学出版社"),
    "华师大版": ("华师大版", "华东师范大学出版社"),
    "统编版": ("统编版", "部编版"),
}
DEFAULT_CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / (
    "china-edu-book-download"
)
DEFAULT_OUTPUT = Path("教材资料库")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "china-edu-book-download-skill/1.0"
)


class SkillError(RuntimeError):
    """Expected, user-facing failure."""


@dataclasses.dataclass(frozen=True)
class AuthContext:
    access_token: str | None = None
    cookie: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.access_token or self.cookie)


@dataclasses.dataclass(frozen=True)
class DownloadResult:
    resource_id: str
    title: str
    ok: bool
    path: str | None = None
    size: int = 0
    sha256: str | None = None
    source_url: str | None = None
    skipped: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def eprint(*values: object) -> None:
    print(*values, file=sys.stderr)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def redact(text: object, auth: AuthContext | None = None) -> str:
    value = str(text)
    if auth and auth.access_token:
        value = value.replace(auth.access_token, "***")
    value = re.sub(r"(?i)(accessToken=)[^&\s]+", r"\1***", value)
    value = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~-]+", r"\1***", value)
    return value


def normalized_text(value: object) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s·•・_\-—–:：,，.。/\\()（）\[\]【】]+", "", text)


def safe_filename(value: str, limit: int = 150) -> str:
    value = re.sub(r"[\x00-\x1f\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = value or "教材"
    stem = Path(value).stem
    if stem.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    if len(value) > limit:
        suffix = Path(value).suffix
        value = value[: max(1, limit - len(suffix))].rstrip() + suffix
    return value


def extract_resource_id(value: str) -> str:
    value = value.strip()
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme and parsed.netloc:
        query = urllib.parse.parse_qs(parsed.query)
        for key in ("contentId", "content_id", "resourceId", "resource_id"):
            candidate = (query.get(key) or [None])[0]
            if candidate and RESOURCE_ID_RE.fullmatch(candidate):
                return candidate.lower()
    match = RESOURCE_ID_RE.search(value)
    if match:
        return match.group(1).lower()
    raise SkillError(f"无法从输入中识别教材资源 ID：{value}")


def split_resource_ids(values: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        for part in re.split(r"[,，\s]+", value.strip()):
            if not part:
                continue
            resource_id = extract_resource_id(part)
            if resource_id not in seen:
                seen.add(resource_id)
                result.append(resource_id)
    return result


def is_official_host(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    return host in ALLOWED_EXACT_HOSTS or any(
        host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES
    )


def ensure_official_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https":
        raise SkillError(f"拒绝非 HTTPS 地址：{url}")
    if parsed.username or parsed.password:
        raise SkillError("拒绝包含用户名或密码的 URL")
    if parsed.port not in (None, 443):
        raise SkillError(f"拒绝非标准端口：{parsed.port}")
    if not is_official_host(parsed.hostname):
        raise SkillError(f"拒绝非 SmartEdu 官方域名：{parsed.hostname or '<empty>'}")
    return url


def quote_url_path(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/:@")
    return urllib.parse.urlunparse(parsed._replace(path=path))


def recursively_decode_json(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and text[0] in "{[\"" and text[-1] in "}]\"":
            try:
                return recursively_decode_json(json.loads(text), depth + 1)
            except (json.JSONDecodeError, TypeError):
                return value
    if isinstance(value, dict):
        return {key: recursively_decode_json(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [recursively_decode_json(item, depth + 1) for item in value]
    return value


def find_access_token(value: Any) -> str | None:
    value = recursively_decode_json(value)
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).replace("-", "_").lower()
            if lowered in {"access_token", "accesstoken"} and isinstance(item, str):
                token = item.strip()
                if token:
                    return token
        for item in value.values():
            found = find_access_token(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_access_token(item)
            if found:
                return found
    return None


def cookies_from_storage_state(state: dict[str, Any]) -> str | None:
    pairs: list[str] = []
    for cookie in state.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if not (
            domain.endswith("smartedu.cn")
            or domain.endswith("cbern.com.cn")
            or domain.endswith("eduyun.cn")
            or domain.endswith("zxx.edu.cn")
        ):
            continue
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        if name:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs) or None


def token_from_storage_state(state: dict[str, Any]) -> str | None:
    for origin in state.get("origins") or []:
        if not isinstance(origin, dict):
            continue
        for entry in origin.get("localStorage") or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            value = entry.get("value")
            if name.startswith("ND_UC_AUTH") or "AUTH" in name.upper():
                found = find_access_token(value)
                if found:
                    return found
    return find_access_token(state)


def read_token_file(path: Path) -> str | None:
    text = path.expanduser().read_text(encoding="utf-8").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    return find_access_token(value) or (value.strip() if isinstance(value, str) else None)


def load_auth(args: argparse.Namespace) -> AuthContext:
    token = getattr(args, "access_token", None) or os.environ.get("SMARTEDU_ACCESS_TOKEN")
    token_file = getattr(args, "access_token_file", None)
    cookie = getattr(args, "cookie", None) or os.environ.get("SMARTEDU_COOKIE")
    storage_state_path = getattr(args, "storage_state", None)

    if not token and token_file:
        token = read_token_file(Path(token_file))

    if storage_state_path:
        state = read_json(Path(storage_state_path).expanduser())
        if not isinstance(state, dict):
            raise SkillError("Playwright storage-state 必须是 JSON 对象")
        token = token or token_from_storage_state(state)
        cookie = cookie or cookies_from_storage_state(state)

    return AuthContext(
        access_token=token.strip() if isinstance(token, str) and token.strip() else None,
        cookie=cookie.strip() if isinstance(cookie, str) and cookie.strip() else None,
    )


class HttpClient:
    def __init__(
        self,
        auth: AuthContext | None = None,
        timeout: int = 45,
        retries: int = 2,
    ) -> None:
        self.auth = auth or AuthContext()
        self.timeout = timeout
        self.retries = retries
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar)
        )

    def headers(self, accept: str = "*/*") -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Referer": "https://basic.smartedu.cn/tchMaterial",
        }
        if self.auth.access_token:
            token = self.auth.access_token
            headers.update(
                {
                    "Authorization": f"Bearer {token}",
                    "accessToken": token,
                    "X-ND-AUTH": f'MAC id="{token}",nonce="0",mac="0"',
                }
            )
        if self.auth.cookie:
            headers["Cookie"] = self.auth.cookie
        return headers

    def open(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        timeout: int | None = None,
    ) -> Any:
        ensure_official_url(url)
        merged = self.headers()
        if headers:
            merged.update(headers)
        request = urllib.request.Request(url, headers=merged, method=method)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.opener.open(request, timeout=timeout or self.timeout)
                ensure_official_url(response.geturl())
                return response
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                retryable = not isinstance(exc, urllib.error.HTTPError) or exc.code in {
                    408,
                    425,
                    429,
                    500,
                    502,
                    503,
                    504,
                }
                if attempt >= self.retries or not retryable:
                    break
                time.sleep(0.6 * (attempt + 1))
        raise SkillError(redact(f"请求失败：{url}：{last_error}", self.auth))

    def get_json(self, url: str) -> Any:
        with self.open(url, headers={"Accept": "application/json,text/plain,*/*"}) as response:
            body = response.read()
        try:
            return json.loads(body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillError(f"服务器未返回有效 JSON：{url}：{exc}") from exc

    def download_pdf(self, url: str, target: Path, overwrite: bool = False) -> dict[str, Any]:
        ensure_official_url(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            size = target.stat().st_size
            with target.open("rb") as handle:
                magic = handle.read(5)
            if magic == b"%PDF-" and size > 5:
                return {
                    "path": str(target),
                    "size": size,
                    "sha256": sha256_file(target),
                    "skipped": True,
                }
            raise SkillError(f"目标文件已存在但不是有效 PDF：{target}")

        part = target.with_name(f".{target.name}.part")
        if part.exists():
            part.unlink()
        digest = hashlib.sha256()
        total = 0
        try:
            with self.open(
                url,
                headers={"Accept": "application/pdf,application/octet-stream,*/*"},
                timeout=max(self.timeout, 90),
            ) as response, part.open("wb") as output:
                first = response.read(1024 * 64)
                if not first.startswith(b"%PDF-"):
                    content_type = response.headers.get("Content-Type", "")
                    preview = first[:120].decode("utf-8", errors="replace")
                    raise SkillError(
                        "响应不是 PDF"
                        f"（Content-Type={content_type!r}，开头={preview!r}）"
                    )
                output.write(first)
                digest.update(first)
                total += len(first)
                while True:
                    chunk = response.read(1024 * 512)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    total += len(chunk)
            if total <= 5:
                raise SkillError("下载得到空 PDF")
            os.replace(part, target)
            return {
                "path": str(target),
                "size": total,
                "sha256": digest.hexdigest(),
                "skipped": False,
            }
        except Exception:
            try:
                part.unlink()
            except FileNotFoundError:
                pass
            raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_version_urls(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    urls = value.get("urls")
    if isinstance(urls, str):
        candidates = [item.strip() for item in urls.split(",") if item.strip()]
    elif isinstance(urls, list):
        candidates = [str(item).strip() for item in urls if str(item).strip()]
    else:
        candidates = []
    result: list[str] = []
    for candidate in candidates:
        candidate = urllib.parse.urljoin(VERSION_URL, candidate)
        ensure_official_url(candidate)
        result.append(candidate)
    return result


def tags_by_dimension(record: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for tag in record.get("tag_list") or []:
        if not isinstance(tag, dict):
            continue
        dimension = TAG_DIMENSIONS.get(str(tag.get("tag_dimension_id") or ""))
        name = str(tag.get("tag_name") or "").strip()
        if dimension and name and dimension not in values:
            values[dimension] = name
    return values


def provider_names(record: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for provider in record.get("provider_list") or []:
        if isinstance(provider, dict) and provider.get("name"):
            names.append(str(provider["name"]))
    if not names and record.get("provider"):
        names.append(str(record["provider"]))
    return names


def normalize_record(record: dict[str, Any]) -> dict[str, Any] | None:
    resource_id = str(record.get("id") or record.get("global_resource_id") or "").strip()
    if not RESOURCE_ID_RE.fullmatch(resource_id):
        return None
    dimensions = tags_by_dimension(record)
    custom = record.get("custom_properties") or {}
    if not isinstance(custom, dict):
        custom = {}
    title = record.get("title")
    if not title and isinstance(record.get("global_title"), dict):
        title = record["global_title"].get("zh-CN")
    title = str(title or resource_id).strip()
    tags = [
        str(tag.get("tag_name"))
        for tag in (record.get("tag_list") or [])
        if isinstance(tag, dict) and tag.get("tag_name")
    ]
    return {
        "id": resource_id.lower(),
        "title": title,
        "description": str(record.get("description") or ""),
        "stage": dimensions.get("stage"),
        "grade": dimensions.get("grade"),
        "subject": dimensions.get("subject"),
        "version": dimensions.get("version"),
        "volume": dimensions.get("volume"),
        "category": dimensions.get("category"),
        "format": custom.get("format"),
        "size": custom.get("size"),
        "providers": provider_names(record),
        "tags": tags,
        "detail_page": DETAIL_PAGE.format(resource_id=resource_id.lower()),
        "create_time": record.get("create_time"),
        "update_time": record.get("update_time"),
        "online_time": record.get("online_time"),
    }


def sync_catalog(cache_dir: Path, client: HttpClient) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    version = client.get_json(VERSION_URL)
    urls = parse_version_urls(version)
    if not urls:
        raise SkillError("教材版本索引中没有分片 URL")

    records: dict[str, dict[str, Any]] = {}
    for index, url in enumerate(urls, start=1):
        eprint(f"[{index}/{len(urls)}] 同步教材索引：{url}")
        part = client.get_json(url)
        if not isinstance(part, list):
            raise SkillError(f"教材索引分片不是数组：{url}")
        for raw in part:
            if not isinstance(raw, dict):
                continue
            normalized = normalize_record(raw)
            if normalized:
                records[normalized["id"]] = normalized

    books = sorted(
        records.values(),
        key=lambda row: tuple(
            normalized_text(row.get(field))
            for field in ("stage", "grade", "subject", "version", "volume", "title")
        ),
    )
    payload = {
        "schema": "china-edu-textbook-catalog/v1",
        "source": "国家中小学智慧教育平台",
        "source_url": "https://basic.smartedu.cn/tchMaterial",
        "generated_at": utc_now(),
        "version_feed": VERSION_URL,
        "count": len(books),
        "books": books,
    }
    write_json(cache_dir / "catalog.json", payload)
    write_json(cache_dir / "version.json", version)
    return payload


def load_catalog(cache_dir: Path, client: HttpClient, refresh: bool = False) -> dict[str, Any]:
    path = cache_dir / "catalog.json"
    if refresh or not path.exists():
        return sync_catalog(cache_dir, client)
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("books"), list):
        raise SkillError(f"缓存格式无效，请重新执行 sync：{path}")
    return payload


def field_aliases(field: str, expected: str) -> tuple[str, ...]:
    expected = expected.strip()
    if field == "version":
        return VERSION_ALIASES.get(expected, (expected,))
    grade_aliases = {
        "高一": ("高一", "高中一年级"),
        "高二": ("高二", "高中二年级"),
        "高三": ("高三", "高中三年级"),
    }
    if field == "grade":
        return grade_aliases.get(expected, (expected,))
    return (expected,)


def matches_field(book: dict[str, Any], field: str, expected: str | None) -> bool:
    if not expected:
        return True
    values = [book.get(field), book.get("title")]
    if field == "version":
        values.extend(book.get("providers") or [])
    haystack = normalized_text(" ".join(str(value or "") for value in values))
    return any(normalized_text(alias) in haystack for alias in field_aliases(field, expected))


def search_catalog(
    books: Sequence[dict[str, Any]],
    *,
    stage: str | None = None,
    grade: str | None = None,
    subject: str | None = None,
    version: str | None = None,
    volume: str | None = None,
    query: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    filters = {
        "stage": stage,
        "grade": grade,
        "subject": subject,
        "version": version,
        "volume": volume,
    }
    query_terms = [normalized_text(term) for term in re.split(r"\s+", query or "") if term]
    matches: list[dict[str, Any]] = []
    for book in books:
        if not all(matches_field(book, field, expected) for field, expected in filters.items()):
            continue
        haystack = normalized_text(json.dumps(book, ensure_ascii=False, sort_keys=True))
        if query_terms and not all(term in haystack for term in query_terms):
            continue
        matches.append(book)
        if limit and len(matches) >= limit:
            break
    return matches


def book_from_id(resource_id: str, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    if catalog:
        for book in catalog.get("books") or []:
            if str(book.get("id") or "").lower() == resource_id.lower():
                return dict(book)
    return {
        "id": resource_id.lower(),
        "title": resource_id.lower(),
        "stage": None,
        "grade": None,
        "subject": None,
        "version": None,
        "volume": None,
        "providers": [],
        "detail_page": DETAIL_PAGE.format(resource_id=resource_id.lower()),
    }


def fetch_detail(resource_id: str, client: HttpClient) -> dict[str, Any]:
    errors: list[str] = []
    for template in DETAIL_URLS:
        url = template.format(resource_id=urllib.parse.quote(resource_id))
        try:
            detail = client.get_json(url)
            if isinstance(detail, dict):
                return detail
            errors.append(f"{url}: 返回值不是对象")
        except SkillError as exc:
            errors.append(redact(exc, client.auth))
    raise SkillError("无法获取教材详情：" + "；".join(errors))


def storage_values(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    storage = item.get("ti_storage")
    if isinstance(storage, str) and storage.strip():
        values.append(storage.strip())
    for value in item.get("ti_storages") or []:
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values


def public_private_variants(url: str, prefer_private: bool) -> Iterator[str]:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in NDR_PRIVATE_HOSTS:
        index = NDR_PRIVATE_HOSTS.index(host)
        public = parsed._replace(netloc=NDR_PUBLIC_HOSTS[index])
        ordered = (url, urllib.parse.urlunparse(public)) if prefer_private else (
            urllib.parse.urlunparse(public),
            url,
        )
        yield from ordered
        return
    if host in NDR_PUBLIC_HOSTS:
        index = NDR_PUBLIC_HOSTS.index(host)
        private = parsed._replace(netloc=NDR_PRIVATE_HOSTS[index])
        ordered = (urllib.parse.urlunparse(private), url) if prefer_private else (
            url,
            urllib.parse.urlunparse(private),
        )
        yield from ordered
        return
    yield url


def expand_storage(value: str, prefer_private: bool) -> Iterator[str]:
    marker = "cs_path:${ref-path}"
    if value.startswith(marker):
        suffix = value[len(marker) :]
        for private, public in zip(NDR_PRIVATE_HOSTS, NDR_PUBLIC_HOSTS):
            hosts = (private, public) if prefer_private else (public, private)
            for host in hosts:
                yield f"https://{host}{suffix}"
        return
    if value.startswith("/"):
        for host in (NDR_PRIVATE_HOSTS if prefer_private else NDR_PUBLIC_HOSTS):
            yield f"https://{host}{value}"
        return
    yield from public_private_variants(value, prefer_private)


def pdf_url_candidates(
    detail: dict[str, Any], resource_id: str, auth: AuthContext | None = None
) -> list[str]:
    items = [item for item in (detail.get("ti_items") or []) if isinstance(item, dict)]
    items.sort(
        key=lambda item: (
            not bool(item.get("ti_is_source_file")),
            str(item.get("ti_file_flag") or "") != "source",
            str(item.get("ti_format") or item.get("lc_ti_format") or "").lower()
            != "pdf",
        )
    )
    prefer_private = bool(auth and auth.access_token)
    candidates: list[str] = []
    for item in items:
        format_name = str(item.get("ti_format") or item.get("lc_ti_format") or "").lower()
        storages = storage_values(item)
        if format_name and format_name != "pdf" and not any(
            urllib.parse.urlparse(value).path.lower().endswith(".pdf") for value in storages
        ):
            continue
        for storage in storages:
            for url in expand_storage(storage, prefer_private):
                try:
                    candidates.append(quote_url_path(ensure_official_url(url)))
                except SkillError:
                    continue
    candidates.append(LEGACY_PDF.format(resource_id=resource_id))
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def metadata_path(book: dict[str, Any], output_dir: Path) -> Path:
    parts = [
        book.get("stage") or "未分类学段",
        book.get("grade") or "未分类年级",
        book.get("subject") or "未分类学科",
        book.get("version") or "未分类版本",
        book.get("volume") or "未分类册次",
    ]
    path = output_dir
    for part in parts:
        path /= safe_filename(str(part), limit=60)
    return path


def download_book(
    book: dict[str, Any],
    output_dir: Path,
    client: HttpClient,
    overwrite: bool = False,
    flat: bool = False,
) -> DownloadResult:
    resource_id = extract_resource_id(str(book.get("id") or ""))
    title = str(book.get("title") or resource_id).strip()
    try:
        detail = fetch_detail(resource_id, client)
        title = str(detail.get("title") or title).strip()
        candidates = pdf_url_candidates(detail, resource_id, client.auth)
        directory = output_dir if flat else metadata_path(book, output_dir)
        filename = safe_filename(f"{title}_{resource_id[:8]}.pdf")
        target = directory / filename
        failures: list[str] = []
        for url in candidates:
            try:
                result = client.download_pdf(url, target, overwrite=overwrite)
                return DownloadResult(
                    resource_id=resource_id,
                    title=title,
                    ok=True,
                    path=result["path"],
                    size=result["size"],
                    sha256=result["sha256"],
                    source_url=url,
                    skipped=bool(result["skipped"]),
                )
            except SkillError as exc:
                failures.append(redact(exc, client.auth))
        raise SkillError(failures[-1] if failures else "没有可用的 PDF 地址")
    except Exception as exc:
        return DownloadResult(
            resource_id=resource_id,
            title=title,
            ok=False,
            error=redact(exc, client.auth),
        )


def candidate_view(book: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "smartedu",
        "source_name": "国家中小学智慧教育平台",
        "source_url": book.get("detail_page"),
        "resource_id": book.get("id"),
        "title": book.get("title"),
        "resource_type": "教材",
        "format": "pdf",
        "stage": book.get("stage"),
        "grade": book.get("grade"),
        "subject": book.get("subject"),
        "version": book.get("version"),
        "volume": book.get("volume"),
        "provider": " / ".join(book.get("providers") or []),
        "official": True,
        "downloadable": True,
        "requires_auth": "may-be-required",
        "size": book.get("size"),
        "raw": book,
    }


def add_filter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stage", "--period", dest="stage", help="学段：小学/初中/高中")
    parser.add_argument("--grade", help="年级：三年级/七年级/高一等")
    parser.add_argument("--subject", help="学科：语文/数学/英语等")
    parser.add_argument("--version", help="教材版本或出版社：人教版/北师大版等")
    parser.add_argument("--volume", help="册次：上册/下册/全一册/必修等")
    parser.add_argument("--query", help="标题及全部元数据关键词，空格分隔时逐词匹配")


def add_cache_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cache-dir", type=Path, default=DEFAULT_CACHE, help=f"索引缓存目录（默认 {DEFAULT_CACHE}）"
    )
    parser.add_argument("--refresh", action="store_true", help="使用前强制刷新教材索引")


def add_auth_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--access-token",
        help="SmartEdu Access Token；优先使用 SMARTEDU_ACCESS_TOKEN 环境变量",
    )
    parser.add_argument("--access-token-file", help="从本地文件读取 Access Token")
    parser.add_argument("--cookie", help="已有浏览器 Cookie；也可使用 SMARTEDU_COOKIE")
    parser.add_argument(
        "--storage-state", help="Playwright storage_state.json；自动提取 Cookie 和 ND_UC_AUTH token"
    )
    parser.add_argument("--timeout", type=int, default=45, help="HTTP 超时秒数")
    parser.add_argument("--retries", type=int, default=2, help="可重试请求的重试次数")


def filter_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "stage": getattr(args, "stage", None),
        "grade": getattr(args, "grade", None),
        "subject": getattr(args, "subject", None),
        "version": getattr(args, "version", None),
        "volume": getattr(args, "volume", None),
        "query": getattr(args, "query", None),
        "limit": getattr(args, "limit", None),
    }


def make_client(args: argparse.Namespace) -> HttpClient:
    return HttpClient(
        auth=load_auth(args),
        timeout=max(1, int(getattr(args, "timeout", 45))),
        retries=max(0, int(getattr(args, "retries", 2))),
    )


def emit(value: Any, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def command_sync(args: argparse.Namespace) -> int:
    client = make_client(args)
    catalog = sync_catalog(args.cache_dir.expanduser(), client)
    summary = {
        "schema": "china-edu-book-sync/v1",
        "count": catalog["count"],
        "cache_file": str(args.cache_dir.expanduser() / "catalog.json"),
        "generated_at": catalog["generated_at"],
    }
    emit(summary, args.json)
    return 0


def command_search(args: argparse.Namespace) -> int:
    client = make_client(args)
    catalog = load_catalog(args.cache_dir.expanduser(), client, refresh=args.refresh)
    matches = search_catalog(catalog["books"], **filter_kwargs(args))
    candidates = [candidate_view(book) for book in matches]
    payload = {
        "schema": "learning-resource-candidate/v1",
        "source": "smartedu",
        "matched": len(candidates),
        "filters": {key: value for key, value in filter_kwargs(args).items() if value is not None},
        "candidates": candidates,
    }
    if args.output:
        write_json(args.output, payload)
    if args.json:
        emit(payload, True)
    else:
        for index, book in enumerate(matches, start=1):
            metadata = " / ".join(
                str(book.get(key) or "-")
                for key in ("stage", "grade", "subject", "version", "volume")
            )
            print(f"{index:>3}. {book.get('title')}")
            print(f"     {metadata}")
            print(f"     ID: {book.get('id')}")
            print(f"     {book.get('detail_page')}")
        eprint(f"匹配 {len(matches)} 本教材")
        if args.output:
            eprint(f"候选清单已写入：{args.output}")
    return 0


def resolve_download_books(
    args: argparse.Namespace, client: HttpClient
) -> tuple[list[dict[str, Any]], bool]:
    direct_values: list[str] = []
    direct_values.extend(args.id or [])
    direct_values.extend(args.url or [])
    resource_ids = split_resource_ids(direct_values)

    has_filters = any(
        getattr(args, name, None)
        for name in ("stage", "grade", "subject", "version", "volume", "query")
    )
    catalog: dict[str, Any] | None = None
    if resource_ids or has_filters:
        try:
            catalog = load_catalog(args.cache_dir.expanduser(), client, refresh=args.refresh)
        except SkillError:
            if has_filters:
                raise
            catalog = None

    if resource_ids:
        return [book_from_id(resource_id, catalog) for resource_id in resource_ids], True
    if not has_filters:
        raise SkillError("download 需要 --id/--url，或至少一个教材筛选条件")
    assert catalog is not None
    matches = search_catalog(catalog["books"], **filter_kwargs(args))
    return matches, False


def command_download(args: argparse.Namespace) -> int:
    client = make_client(args)
    books, explicit_ids = resolve_download_books(args, client)
    if not books:
        raise SkillError("没有匹配的教材")
    if not explicit_ids and len(books) > 1 and not args.all:
        preview = "\n".join(
            f"  {index}. {book.get('title')} [{book.get('id')}]"
            for index, book in enumerate(books[:20], start=1)
        )
        suffix = "\n  ..." if len(books) > 20 else ""
        raise SkillError(
            f"筛选条件匹配 {len(books)} 本教材。为避免误下载，请补充筛选条件，"
            f"或确认后添加 --all。\n{preview}{suffix}"
        )
    if args.max_books and len(books) > args.max_books:
        books = books[: args.max_books]

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[DownloadResult] = []
    workers = max(1, min(args.workers, 8))

    def work(book: dict[str, Any]) -> DownloadResult:
        eprint(f"下载：{book.get('title')} [{book.get('id')}]")
        return download_book(
            book,
            output_dir=output_dir,
            client=client,
            overwrite=args.overwrite,
            flat=args.flat,
        )

    if workers == 1 or len(books) == 1:
        results = [work(book) for book in books]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(work, book) for book in books]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
        order = {str(book.get("id")): index for index, book in enumerate(books)}
        results.sort(key=lambda item: order.get(item.resource_id, len(order)))

    payload = {
        "schema": "china-edu-book-download/v1",
        "matched": len(books),
        "downloaded": sum(1 for item in results if item.ok and not item.skipped),
        "skipped": sum(1 for item in results if item.ok and item.skipped),
        "failed": sum(1 for item in results if not item.ok),
        "output_dir": str(output_dir),
        "auth_configured": client.auth.configured,
        "files": [item.as_dict() for item in results],
    }
    manifest = output_dir / "download-manifest.json"
    write_json(manifest, payload)
    payload["manifest"] = str(manifest)
    emit(payload, args.json)
    return 0 if payload["failed"] == 0 else 1


def command_doctor(args: argparse.Namespace) -> int:
    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "name": "python",
            "ok": sys.version_info >= (3, 10),
            "detail": sys.version.split()[0],
        }
    )
    cache_dir = args.cache_dir.expanduser()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache_dir, delete=True) as handle:
            handle.write(b"ok")
        checks.append({"name": "cache_writable", "ok": True, "detail": str(cache_dir)})
    except OSError as exc:
        checks.append({"name": "cache_writable", "ok": False, "detail": str(exc)})

    client = make_client(args)
    checks.append(
        {
            "name": "auth",
            "ok": True,
            "detail": "configured" if client.auth.configured else "not configured (public index still works)",
        }
    )
    if args.network:
        try:
            version = client.get_json(VERSION_URL)
            urls = parse_version_urls(version)
            checks.append(
                {
                    "name": "smartedu_index",
                    "ok": bool(urls),
                    "detail": f"{len(urls)} index part(s)",
                }
            )
        except Exception as exc:
            checks.append(
                {
                    "name": "smartedu_index",
                    "ok": False,
                    "detail": redact(exc, client.auth),
                }
            )
    ok = all(check["ok"] for check in checks)
    payload = {"schema": "china-edu-book-doctor/v1", "ok": ok, "checks": checks}
    emit(payload, args.json)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检索并下载国家中小学智慧教育平台中的中国教材 PDF",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="同步公开教材索引到本地缓存")
    sync.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    add_auth_arguments(sync)
    sync.add_argument("--json", action="store_true")
    sync.set_defaults(func=command_sync)

    search = subparsers.add_parser("search", aliases=["list"], help="按元数据检索教材")
    add_filter_arguments(search)
    add_cache_arguments(search)
    add_auth_arguments(search)
    search.add_argument("--limit", type=int, default=50, help="最多返回多少条；0 表示不限")
    search.add_argument("-o", "--output", type=Path, help="将标准候选 JSON 写入文件")
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=command_search)

    download = subparsers.add_parser("download", help="下载精确 ID 或筛选条件匹配的教材")
    add_filter_arguments(download)
    add_cache_arguments(download)
    add_auth_arguments(download)
    download.add_argument("--id", action="append", help="教材 UUID；可重复或以逗号分隔")
    download.add_argument("--url", action="append", help="SmartEdu 教材详情页 URL；可重复")
    download.add_argument("--all", action="store_true", help="确认下载筛选条件匹配的全部教材")
    download.add_argument("--max-books", type=int, help="确认后最多下载前 N 本")
    download.add_argument("--limit", type=int, default=None, help="检索阶段最多匹配 N 条")
    download.add_argument("-o", "--output-dir", type=Path, default=DEFAULT_OUTPUT)
    download.add_argument("--flat", action="store_true", help="不按学段/年级/学科等分目录")
    download.add_argument("--overwrite", action="store_true", help="覆盖现有 PDF")
    download.add_argument("--workers", type=int, default=2, help="并发下载数（1-8）")
    download.add_argument("--json", action="store_true")
    download.set_defaults(func=command_download)

    doctor = subparsers.add_parser("doctor", help="检查运行环境和可选网络连通性")
    doctor.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    add_auth_arguments(doctor)
    doctor.add_argument("--network", action="store_true", help="同时请求 SmartEdu 公开索引")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=command_doctor)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "limit") and args.limit == 0:
        args.limit = None
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        eprint("已中断")
        return 130
    except SkillError as exc:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"schema": "china-edu-book-error/v1", "ok": False, "error": str(exc)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            eprint(f"错误：{exc}")
        return 2
    except Exception as exc:  # pragma: no cover - final safety net
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"schema": "china-edu-book-error/v1", "ok": False, "error": redact(exc)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            eprint(f"未预期错误：{redact(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
