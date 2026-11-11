"""CalDAV privado do iCloud. Só envia credenciais a endpoints HTTPS do calendário Apple."""

import re
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree as ET

import requests

DAV = "{DAV:}"
CAL = "{urn:ietf:params:xml:ns:caldav}"
BASE = "https://caldav.icloud.com/"
HOST = re.compile(r"(?:p[0-9]+-)?caldav\.icloud\.com")
LIMIT = 16 * 1024 * 1024


class AppleError(Exception):
    def __init__(self, status=0):
        self.status = status
        message = {
            401: "O iCloud recusou as credenciais. Confira a Conta Apple e a senha específica do app.",
            403: "O iCloud recusou o acesso. Confira as permissões da agenda e a senha específica.",
            409: "O compromisso foi alterado no iCloud.",
            412: "O compromisso foi alterado no iCloud.",
        }.get(
            status,
            "Não foi possível sincronizar com o iCloud. Confira sua conexão e tente novamente.",
        )
        super().__init__(message)


def trusted(base, href):
    url = urljoin(base, href)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not HOST.fullmatch(parsed.hostname or "")
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
    ):
        raise AppleError()
    return url


def member_url(collection, filename):
    if (
        not filename
        or any(c in unquote(filename) for c in "/\\?#")
        or unquote(filename) in (".", "..")
    ):
        raise AppleError()
    return trusted(collection.rstrip("/") + "/", filename)


@dataclass(frozen=True)
class RemoteCalendar:
    url: str
    name: str
    writable: bool


class CalDAV:
    def __init__(self, account: str, password: str):
        self.auth = (account, password)

    def request(self, method, url, *, data=None, headers=None):
        url = trusted(BASE, url)
        for _ in range(5):
            try:
                with requests.request(
                    method,
                    url,
                    auth=self.auth,
                    data=data,
                    headers=headers,
                    allow_redirects=False,
                    timeout=(5, 20),
                    stream=True,
                ) as res:
                    if res.status_code in (301, 302, 307, 308) and "Location" in res.headers:
                        url = trusted(url, res.headers["Location"])
                        continue
                    if not 200 <= res.status_code < 300:
                        raise AppleError(res.status_code)
                    buf = bytearray()
                    for chunk in res.iter_content(65536):
                        buf.extend(chunk)
                        if len(buf) > LIMIT:
                            raise AppleError()
                    return url, res.headers, bytes(buf)
            except requests.RequestException:
                raise AppleError() from None
        raise AppleError()

    def properties(self, url, depth, names):
        query = ET.Element(DAV + "propfind")
        props = ET.SubElement(query, DAV + "prop")
        for tag in names:
            ET.SubElement(props, tag)
        base, _, raw = self.request(
            "PROPFIND",
            url,
            data=ET.tostring(query, encoding="utf-8"),
            headers={"Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"},
        )
        if b"<!DOCTYPE" in raw.upper():
            raise AppleError()
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise AppleError() from None
        if root.tag != DAV + "multistatus":
            raise AppleError()
        rows = []
        for row in root.findall(DAV + "response"):
            href = row.findtext(DAV + "href")
            if not href:
                raise AppleError()
            result = {}
            for stat in row.findall(DAV + "propstat"):
                if " 200 " in (stat.findtext(DAV + "status") or ""):
                    result.update({p.tag: p for p in stat.findall(DAV + "prop/*")})
            if not result:
                raise AppleError()
            rows.append((trusted(base, href), result))
        return rows

    def href(self, url, prop):
        rows = self.properties(url, 0, [prop])
        for origin, fields in rows:
            if prop in fields and (href := fields[prop].findtext(DAV + "href")):
                return trusted(origin, href)
        raise AppleError()

    def calendars(self):
        principal = self.href(BASE, DAV + "current-user-principal")
        home = self.href(principal, CAL + "calendar-home-set")
        rows = self.properties(
            home,
            1,
            [
                DAV + "resourcetype",
                DAV + "displayname",
                DAV + "current-user-privilege-set",
                CAL + "supported-calendar-component-set",
            ],
        )
        calendars = []
        for url, fields in rows:
            types = fields.get(DAV + "resourcetype")
            if types is None or types.find(CAL + "calendar") is None:
                continue
            components = fields.get(CAL + "supported-calendar-component-set")
            if components is not None and not any(c.get("name") == "VEVENT" for c in components):
                continue
            priv = fields.get(DAV + "current-user-privilege-set")
            granted = {p.tag for p in priv.iter()} if priv is not None else set()
            label = fields.get(DAV + "displayname")
            calendars.append(
                RemoteCalendar(
                    url.rstrip("/") + "/",
                    (label.text if label is not None else None) or "Calendário iCloud",
                    bool(granted & {DAV + "write", DAV + "all", DAV + "write-content"}),
                )
            )
        return calendars

    def members(self, url):
        rows = self.properties(url, 1, [DAV + "resourcetype", DAV + "getetag"])
        if not any(href.rstrip("/") == url.rstrip("/") for href, _ in rows):
            raise AppleError()
        result = {}
        for href, attrs in rows:
            if href.rstrip("/") == url.rstrip("/"):
                continue
            types = attrs.get(DAV + "resourcetype")
            if types is not None and types.find(DAV + "collection") is not None:
                continue
            key = href.rsplit("/", 1)[-1]
            tag = attrs.get(DAV + "getetag")
            if member_url(url, key) != href or tag is None or not tag.text:
                raise AppleError()
            result[key] = tag.text
        return result

    def get(self, url):
        _, headers, raw = self.request("GET", url)
        etag = headers.get("ETag")
        if not etag:
            raise AppleError()
        return etag, raw.decode("utf-8-sig")

    def put(self, url, body, etag):
        condition = {"If-Match": etag} if etag else {"If-None-Match": "*"}
        self.request(
            "PUT",
            url,
            data=body,
            headers={"Content-Type": "text/calendar; charset=utf-8", **condition},
        )

    def delete(self, url, etag):
        self.request("DELETE", url, headers={"If-Match": etag})
