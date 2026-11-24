"""读取公网链接的网页正文或文件摘录，供用户确认后收藏。

边界：
- 只抓静态 HTML。常见内容站（微信 / B站专栏 / CSDN / 简书 / 掘金等）用站点专属选择器优先匹配正文；
- 知乎 / 小红书 / 抖音等强反爬或纯 JS 渲染的站点仍然抓不到。
- 纯 JS 渲染的页面（前端拉数据）会抓成空壳。
- 必须防 SSRF：只允许 http/https，拒绝内网 / 回环 / 链路本地地址，重定向逐跳校验。
- 不联网搜索、不调大模型：抓回来的文字原样给用户看，用户确认后才入库。
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from contextlib import asynccontextmanager
from email.message import Message
from email.policy import default as email_policy
from pathlib import PurePosixPath
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.services import extract as extractor

MAX_WEB_BYTES = 3 * 1024 * 1024
MAX_CHARS = 60000
TIMEOUT = 5
DOWNLOAD_TIMEOUT = 30
MAX_REDIRECTS = 5
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_DROP_TAGS = (
    "script", "style", "noscript", "iframe", "svg", "canvas", "nav",
    "header", "footer", "aside", "form", "button", "select", "input",
)
_BLOCK_TAGS = (
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote",
    "pre", "td", "dd", "dt", "figcaption",
)


class FetchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def _public_addresses(url: httpx.URL) -> list[str]:
    """返回经过公网地址校验的连接目标。"""
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        raise FetchError("FETCH_INVALID_URL", "请输入有效的 HTTP 或 HTTPS 网址")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            url.raw_host.decode("ascii"), None, type=socket.SOCK_STREAM,
        )
    except (OSError, UnicodeError) as exc:
        raise FetchError("FETCH_FAILED", "找不到网址对应的服务器") from exc
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_reserved or ip.is_multicast:
            raise FetchError("FETCH_BLOCKED", "只能读取公网地址")
    if not addresses:
        raise FetchError("FETCH_FAILED", "找不到网址对应的服务器")
    return addresses


_SITE_SELECTORS: dict[str, tuple[str, ...]] = {
    "mp.weixin.qq.com": ("#js_content", "#page-content", ".rich_media_content"),
    "bilibili.com": (".article-content", "#read-article-holder", ".opus-module-content", ".article-holder"),
    "zhihu.com": (".RichText", ".Post-RichText"),
    "csdn.net": ("#content_views", ".blog-content-box", ".htmledit_views"),
    "jianshu.com": (".show-content", "#note-holder"),
    "douban.com": ("#link-report", ".note-content", ".review-content"),
    "sspai.com": (".article-content", "#article-content", ".content--article"),
    "juejin.cn": (".markdown-body", "#article-root"),
    "segmentfault.com": (".article-content", ".fmt"),
    "cnblogs.com": ("#cnblogs_post_body", ".postBody"),
    "oschina.net": (".article-detail", "#articleContent"),
    "xueqiu.com": (".article__bd__detail", ".article"),
    "weibo.com": (".WB_text", ".detail_wbtext", ".article"),
    "weibo.cn": (".weibo-text", "#M_" ),
    "toutiao.com": (".article-content", ".syl-article-base"),
    "36kr.com": (".articleDetailContent", ".article-content"),
    "huxiu.com": (".article__content", ".article-content"),
    "ithome.com": ("#paragraph", ".post_content"),
    "infoq.cn": (".article-preview", ".article"),
    "qianjia.com": (".article", ".content"),
}


def _site_selectors(host: str) -> tuple[str, ...]:
    h = (host or "").lower()
    for domain, sels in _SITE_SELECTORS.items():
        if h == domain or h.endswith("." + domain):
            return sels
    return ()


def _clean(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join([ln for ln in lines if ln])


_NOISE_LINES = {
    "预览时标签不可点", "继续滑动看下一个", "轻触阅读原文", "向上滑动看下一个",
    "微信扫一扫关注该公众号", "微信扫一扫", "关注该公众号", "知道了",
    "长按识别二维码关注", "分享", "收藏", "赞", "在看", "留言", "听过",
    "阅读原文", "写下你的留言", "取消", "允许", "视频", "小程序",
}


def _strip_noise(text: str) -> str:
    """去掉公众号 / 移动端页面上不属于正文的固定文案与纯符号行。"""
    out: list[str] = []
    for line in text.splitlines():
        t = line.strip()
        if not t or t in _NOISE_LINES:
            continue
        if re.fullmatch(r"[\s·•\-—_=*~#>|.。,，、]{1,}", t):
            continue
        out.append(t)
    return "\n".join(out)


_WX_CONTENT_RE = re.compile(r"content_noencode\s*:\s*(['\"])(.*?)\1", re.S)


def _js_unescape(text: str) -> str:
    """还原 JS 字符串里的 \x0a / \x27 / \n 之类转义。"""
    def _rep(m):
        tok = m.group(1)
        if len(tok) == 3 and tok[0] in "xX":
            try:
                return chr(int(tok[1:], 16))
            except ValueError:
                return tok
        return {"n": "\n", "t": "\t", "r": "\r"}.get(tok, tok)

    return re.sub(r"\\(x[0-9a-fA-F]{2}|.)", _rep, text)


def _wx_embedded(html: str) -> str:
    """新版微信文章页 DOM 是空壳，正文只在内嵌 JS 对象的 content_noencode 里。"""
    m = _WX_CONTENT_RE.search(html)
    if not m:
        return ""
    raw = _js_unescape(m.group(2))
    if "<" in raw and ">" in raw:
        raw = BeautifulSoup(raw, "lxml").get_text("\n", strip=True)
    return _strip_noise(_clean(raw))


def _extract(html: str, host: str = "") -> tuple[str, str]:
    """返回 (标题, 正文)。"""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            title = str(og["content"]).strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(" ", strip=True)
    title = re.sub(r"\s+", " ", title)[:120]

    node = None
    for sel in _site_selectors(host):
        found = soup.select_one(sel)
        if found and len(found.get_text(strip=True)) > 20:
            node = found
            break
    if node is None:
        for sel in ("article", "main", '[role="main"]', "#content",
                    ".article-content", ".post-content", ".markdown-body", "body"):
            found = soup.select_one(sel)
            if found and len(found.get_text(strip=True)) > 80:
                node = found
                break
    node = node or soup.body or soup

    seen: list[str] = []
    for el in node.find_all(_BLOCK_TAGS):
        text = _clean(el.get_text(" ", strip=True))
        if len(text) < 2:
            continue
        if any(text in s for s in seen):
            continue
        seen = [s for s in seen if s not in text]
        seen.append(text)
    body = "\n\n".join(seen)
    full = _clean(node.get_text("\n", strip=True))
    # 公众号常用 <section> 套排版，文字不在 <p> 里；块级提取覆盖不足时改用全文本
    if len(body) < 40 or (len(full) > 200 and len(body) < len(full) * 0.55):
        body = _strip_noise(full)
    else:
        body = _strip_noise(body)
    if len(body) < 200 and "weixin.qq.com" in host.lower():
        wx = _wx_embedded(html)
        if len(wx) > len(body):
            body = wx
    return title, body


def _decode(data: bytes, ctype: str) -> str:
    """按 header 或 meta 声明的编码解码；都没有就先 utf-8 再 gb18030。"""
    enc = None
    m = re.search(r"charset=([\w-]+)", ctype or "")
    if m:
        enc = m.group(1)
    if not enc:
        m = re.search(rb'charset=["\']?([\w-]+)', data[:4096], re.I)
        if m:
            enc = m.group(1).decode("ascii", "ignore")
    for candidate in ([enc] if enc else []) + ["utf-8", "gb18030"]:
        if not candidate:
            continue
        try:
            return data.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


async def _request_public(transport: httpx.AsyncHTTPTransport, url: httpx.URL) -> httpx.Response:
    last_error = None
    for address in await _public_addresses(url):
        # 连接已校验的 IP，同时按原域名验证 TLS 证书并发送 Host。
        request = httpx.Request(
            "GET", url.copy_with(host=address),
            headers={
                "Host": url.netloc,
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "identity",
            },
            extensions={"sni_hostname": url.raw_host.decode("ascii"), "timeout": httpx.Timeout(TIMEOUT).as_dict()},
        )
        try:
            return await transport.handle_async_request(request)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_error = exc
    if isinstance(last_error, httpx.TimeoutException):
        raise last_error
    raise FetchError("FETCH_FAILED", "无法连接链接服务器") from last_error


@asynccontextmanager
async def _public_response(raw_url: str):
    """逐跳校验公网地址，并为建立连接、重定向和读取正文设置总时限。"""
    try:
        url = httpx.URL(raw_url)
        async with asyncio.timeout(DOWNLOAD_TIMEOUT):
            for _ in range(MAX_REDIRECTS + 1):
                # 每跳使用独立连接，避免不同域名共用同一个 IP 时复用 TLS 连接。
                async with httpx.AsyncHTTPTransport(trust_env=False) as transport:
                    response = await _request_public(transport, url)
                    try:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise FetchError("FETCH_FAILED", "页面跳转但没有目标网址")
                            url = url.join(location)
                            continue
                        if response.status_code in {401, 403}:
                            raise FetchError("FETCH_FORBIDDEN", "链接需要访问权限。可在原网站下载后导入文件")
                        if not response.is_success:
                            raise FetchError("FETCH_FAILED", f"链接返回 HTTP {response.status_code}")
                        yield url, response
                        return
                    finally:
                        await response.aclose()
    except httpx.InvalidURL as exc:
        raise FetchError("FETCH_INVALID_URL", "请输入有效的 HTTP 或 HTTPS 网址") from exc
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise FetchError("FETCH_TIMEOUT", "读取链接超时，可以稍后重试") from exc
    except httpx.HTTPError as exc:
        raise FetchError("FETCH_FAILED", "无法读取链接内容") from exc
    raise FetchError("FETCH_FAILED", "链接跳转次数过多")


async def _read_body(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    data = bytearray()
    async for chunk in response.aiter_bytes():
        remaining = limit - len(data)
        if len(chunk) > remaining:
            data.extend(chunk[:remaining])
            return bytes(data), True
        data.extend(chunk)
    return bytes(data), False


_FILE_MEDIA_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/csv": ".csv",
    "text/markdown": ".md",
}
_FILE_SUFFIXES = extractor.TEXT_SUFFIXES | set(_FILE_MEDIA_TYPES.values())
_HTML_TYPES = {"text/html", "application/xhtml+xml"}


def _filename(url: httpx.URL, content_type: str, disposition: str) -> str | None:
    """根据文件响应的类型、下载名和实际网址确定解析格式。"""
    if content_type in _HTML_TYPES:
        return None
    headers = Message(policy=email_policy)
    headers["Content-Disposition"] = disposition
    url_name = PurePosixPath(url.path).name
    name = (headers.get_filename() or url_name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(character for character in name if character.isprintable()).strip()
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in _FILE_SUFFIXES:
        suffix = PurePosixPath(url_name).suffix.lower()
    suffix = _FILE_MEDIA_TYPES.get(content_type, suffix)
    if suffix not in _FILE_SUFFIXES:
        if content_type != "text/plain":
            return None
        suffix = ".txt"
    stem = PurePosixPath(name).stem or "材料"
    extension = name[-len(suffix):] if name.lower().endswith(suffix) else suffix
    return stem[:200 - len(extension)] + extension


_MSN_HOSTS = ("msn.cn", "msn.com")
_MSN_ID_RE = re.compile(r"/ar-([A-Za-z0-9]{6,})")


async def _msn_article(final_url: str) -> dict | None:
    """MSN 正文由前端渲染，HTML 是空壳；改走它的内容接口拿 JSON。

    assets.msn.com/content/view/v2/Detail/{locale}/{articleId} 这个接口不需要 key，
    返回里 body 是 HTML 片段。解析失败就返回 None，让上层走普通 HTML 提取。
    """
    parsed = urlparse(final_url)
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _MSN_HOSTS):
        return None
    m = _MSN_ID_RE.search(parsed.path or "")
    if not m:
        return None
    article_id = m.group(1)
    lm = re.match(r"^/([a-z]{2}-[a-z]{2})/", parsed.path or "")
    locale = lm.group(1) if lm else "zh-cn"
    api = "https://assets.msn.com/content/view/v2/Detail/%s/%s" % (locale, article_id)
    try:
        async with _public_response(api) as (_, response):
            data, truncated = await _read_body(response, MAX_WEB_BYTES)
        if truncated:
            return None
        doc = json.loads(data)
    except (FetchError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None

    body = doc.get("body") or ""
    if not isinstance(body, str) or not body.strip():
        body = doc.get("abstract") or ""
    text = ""
    if isinstance(body, str) and body.strip():
        text = _clean(BeautifulSoup(body, "lxml").get_text("\n", strip=True))
    if len(text) < 40:
        return None
    title = _clean(str(doc.get("title") or ""))[:120]
    return {"title": title, "text": text}


async def fetch_url(url: str) -> dict:
    """读取链接，文件返回提取结果，网页返回正文。"""
    raw = (url or "").strip()
    if not raw:
        raise FetchError("FETCH_INVALID_URL", "请先填链接")
    if len(raw) > 2000:
        raise FetchError("FETCH_INVALID_URL", "链接超过 2000 字符，请使用原文件地址")
    if "://" not in raw:
        raw = "https://" + raw

    async with _public_response(raw) as (url, response):
        final_url = str(url)
        ctype = response.headers.get("content-type", "").lower()
        media_type = ctype.split(";", 1)[0].strip()
        filename = _filename(url, media_type, response.headers.get("content-disposition", ""))
        if filename:
            encoding = response.headers.get("content-encoding", "identity").strip().lower()
            if encoding not in {"", "identity"}:
                raise FetchError("FETCH_UNSUPPORTED", "下载地址返回了压缩响应，请先下载文件再导入")
        else:
            if media_type in _HTML_TYPES and PurePosixPath(httpx.URL(raw).path).suffix.lower() in _FILE_SUFFIXES:
                raise FetchError("FETCH_UNSUPPORTED", "文件链接返回了网页。请确认可以直接下载，或下载后导入")
            if media_type and not (media_type.startswith("text/") or "xml" in media_type):
                raise FetchError("FETCH_UNSUPPORTED", "请使用网页链接或可直接下载的 PDF、DOCX、PPTX 或文本文件链接")
        data, truncated = await _read_body(response, extractor.MAX_BYTES if filename else MAX_WEB_BYTES)

    if filename:
        if truncated:
            raise extractor.ExtractError("PAYLOAD_TOO_LARGE", "文件超过 20 MB，请拆分后再导入")
        return {"url": raw, "final_url": final_url, "kind": "file", "file": extractor.extract(filename, data)}

    msn = await _msn_article(final_url)
    if msn is not None:
        text = msn["text"]
        truncated = len(text) > MAX_CHARS
        if truncated:
            text = text[:MAX_CHARS]
        return {
            "url": raw,
            "final_url": final_url,
            "title": msn["title"] or urlparse(final_url).netloc,
            "text": text,
            "chars": len(text),
            "truncated": truncated,
            "kind": "web",
        }

    html = _decode(data, ctype)

    title, text = await asyncio.to_thread(_extract, html, urlparse(final_url).netloc)
    truncated = truncated or len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS]
    if len(text) < 40:
        raise FetchError(
            "FETCH_EMPTY",
            "没读到正文。这个站点可能要登录、或有反爬、或是纯 JS 渲染——请直接把文字粘进摘录。",
        )
    return {
        "url": raw,
        "final_url": final_url,
        "title": title or urlparse(final_url).netloc,
        "text": text,
        "chars": len(text),
        "truncated": truncated,
        "kind": "web",
    }
