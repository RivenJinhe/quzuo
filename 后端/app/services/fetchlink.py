"""链接抓取（BE-021 ~ BE-028）：取网页正文，供材料入库。

边界：
- 只抓静态 HTML。常见内容站（微信 / B站专栏 / CSDN / 简书 / 掘金等）用站点专属选择器优先匹配正文；
- 知乎 / 小红书 / 抖音等强反爬或纯 JS 渲染的站点仍然抓不到。
- 纯 JS 渲染的页面（前端拉数据）会抓成空壳。
- 必须防 SSRF：只允许 http/https，拒绝内网 / 回环 / 链路本地地址，重定向逐跳校验。
- 不联网搜索、不调大模型：抓回来的文字原样给用户看，用户确认后才入库。
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

MAX_BYTES = 3 * 1024 * 1024
MAX_CHARS = 60000
TIMEOUT = 5
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


def _assert_public(host: str) -> None:
    """拒绝内网 / 回环 / 链路本地 / 保留地址，防 SSRF。"""
    if not host:
        raise FetchError("FETCH_INVALID_URL", "链接里没有主机名")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise FetchError("FETCH_FAILED", "域名解析不了：%s" % host) from None
    for _fam, _type, _proto, _canon, sockaddr in infos:
        raw = sockaddr[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise FetchError("FETCH_BLOCKED", "这个地址指向内网，不允许抓取")


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


def _get(url: str) -> tuple[str, str, bytes]:
    """手动跟重定向，每跳校验目标地址；返回 (最终 URL, content-type, body)。"""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        if parsed.scheme not in ("http", "https"):
            raise FetchError("FETCH_INVALID_URL", "只支持 http / https 链接")
        _assert_public(parsed.hostname or "")
        try:
            with httpx.Client(
                timeout=TIMEOUT,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
            ) as client:
                resp = client.get(current)
        except httpx.TimeoutException:
            raise FetchError("FETCH_TIMEOUT", "网页 %d 秒没响应" % TIMEOUT) from None
        except httpx.HTTPError as exc:
            raise FetchError("FETCH_FAILED", "抓取失败：%s" % exc.__class__.__name__) from None

        if resp.is_redirect:
            loc = resp.headers.get("location", "")
            if not loc:
                raise FetchError("FETCH_FAILED", "页面跳转但没有目标地址")
            current = httpx.URL(current).join(loc).__str__()
            continue

        ctype = (resp.headers.get("content-type") or "").lower()
        data = resp.content
        if len(data) > MAX_BYTES:
            data = data[:MAX_BYTES]
        return current, ctype, data

    raise FetchError("FETCH_FAILED", "跳转次数过多")


_MSN_HOSTS = ("msn.cn", "msn.com")
_MSN_ID_RE = re.compile(r"/ar-([A-Za-z0-9]{6,})")


def _msn_article(final_url: str) -> dict | None:
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
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            resp = client.get(api)
        if resp.status_code != 200:
            return None
        doc = resp.json()
    except (httpx.HTTPError, ValueError):
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


def fetch_url(url: str) -> dict:
    """抓一个链接，返回 {url, final_url, title, text, chars, truncated, kind}。"""
    raw = (url or "").strip()
    if not raw:
        raise FetchError("FETCH_INVALID_URL", "请先填链接")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw

    final_url, ctype, data = _get(raw)

    if "pdf" in ctype:
        raise FetchError("FETCH_UNSUPPORTED", "这是一个 PDF 链接，请下载后上传文件")
    if ctype and not any(k in ctype for k in ("text/", "html", "xml")):
        raise FetchError("FETCH_UNSUPPORTED", "这个链接不是网页（%s）" % ctype.split(";")[0])

    msn = _msn_article(final_url)
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

    title, text = _extract(html, urlparse(final_url).netloc)
    truncated = len(text) > MAX_CHARS
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
