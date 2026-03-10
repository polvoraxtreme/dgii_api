"""
dgii_service.py
~~~~~~~~~~~~~~~
Scrapes dgii.gov.do to resolve a RNC/Cédula and validate an NCF.
No external deps — only Python stdlib (urllib, html.parser, re).

Usage:
    from .dgii_service import DgiiService

    result = DgiiService.query_rnc('101234567')
    if result['success']:
        name = result['name']
        status = result['status_raw']   # 'ACTIVO' | 'DADO DE BAJA'

    result = DgiiService.validate_ncf('101234567', 'B0100000001')
    if result['is_valid']:
        ...
"""

import logging
import re
import urllib.parse
import urllib.request
import urllib.error
from html.parser import HTMLParser
from datetime import datetime

_logger = logging.getLogger(__name__)

_BASE_URL  = "https://dgii.gov.do/app/WebApps/ConsultasWeb2/ConsultasWeb/consultas/"
_RNC_PAGE  = _BASE_URL + "rnc.aspx"
_NCF_PAGE  = _BASE_URL + "ncf.aspx"
_TIMEOUT   = 12

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OdooBot/1.0)",
    "Accept":     "text/html,application/xhtml+xml,*/*",
    "Cache-Control": "no-cache",
}

# ---------------------------------------------------------------------------
# Minimal HTML parser
# ---------------------------------------------------------------------------

class _Parser(HTMLParser):
    """Single-pass ASP.NET WebForms parser.
    Collects ViewState hidden inputs and span/label text by id.
    """
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._inputs: dict = {}   # id -> value
        self._spans:  dict = {}   # id -> text
        self._cur_id = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        tag = tag.lower()
        if tag == "input":
            self._inputs[a.get("id", "")] = a.get("value", "")
        if tag in ("span", "label"):
            self._cur_id = a.get("id")

    def handle_endtag(self, tag):
        if tag.lower() in ("span", "label"):
            self._cur_id = None

    def handle_data(self, data):
        if self._cur_id:
            self._spans[self._cur_id] = self._spans.get(self._cur_id, "") + data

    # --- helpers ---
    def viewstate(self) -> dict:
        out = {"__EVENTTARGET": "", "__EVENTARGUMENT": ""}
        for k, v in self._inputs.items():
            if "VIEWSTATEGENERATOR" in k:
                out["__VIEWSTATEGENERATOR"] = v
            elif "VIEWSTATE" in k:
                out["__VIEWSTATE"] = v
            elif "EVENTVALIDATION" in k:
                out["__EVENTVALIDATION"] = v
        return out

    def span(self, partial_id: str) -> str:
        for k, v in self._spans.items():
            if partial_id.lower() in k.lower():
                return v.strip()
        return ""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        cs = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(cs, errors="replace")


def _post(url: str, fields: dict) -> str:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        **_HEADERS,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        cs = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(cs, errors="replace")


def _parse(html: str) -> _Parser:
    p = _Parser()
    p.feed(html)
    return p


def _vs(url: str) -> dict:
    """GET the page, return its viewstate dict."""
    return _parse(_get(url)).viewstate()


# ---------------------------------------------------------------------------
# RNC table extraction
# The DGII RNC page renders a <table> with <b>Label</b> / value cell pairs.
# ---------------------------------------------------------------------------

def _cell_after_bold(html: str, bold_text: str) -> str:
    # Try new DGII format: <td style="font-weight:bold;">Label</td><td>Value</td>
    m = re.search(
        r'<td[^>]*font-weight:\s*bold[^>]*>[^<]*' + re.escape(bold_text) + r'[^<]*</td>\s*<td[^>]*>(.*?)</td>',
        html, re.IGNORECASE | re.DOTALL
    )
    # Fallback to old format: <b>Label</b></td><td>Value</td>
    if not m:
        m = re.search(
            r'<b[^>]*>[^<]*' + re.escape(bold_text) + r'[^<]*</b>\s*</td>\s*<td[^>]*>(.*?)</td>',
            html, re.IGNORECASE | re.DOTALL
        )
    if not m:
        return ""
    raw = re.sub(r'<[^>]+>', '', m.group(1))
    return _ent(raw.strip())


def _ent(text: str) -> str:
    """Decode basic HTML entities."""
    _MAP = {
        "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
        "&#39;": "'", "&nbsp;": " ",
        "&aacute;": "á", "&eacute;": "é", "&iacute;": "í",
        "&oacute;": "ó", "&uacute;": "ú", "&ntilde;": "ñ",
        "&Aacute;": "Á", "&Eacute;": "É", "&Iacute;": "Í",
        "&Oacute;": "Ó", "&Uacute;": "Ú", "&Ntilde;": "Ñ",
        "&uuml;": "ü", "&Uuml;": "Ü",
    }
    for e, c in _MAP.items():
        text = text.replace(e, c)
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    return text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class DgiiService:

    @staticmethod
    def query_rnc(rnc: str) -> dict:
        """
        Returns dict:
            success       bool
            rnc           str
            name          str   – Nombre / Razón Social
            name_commercial str
            contributor_type str  – Régimen de pagos
            commercial_type  str  – Actividad económica
            status_raw    str   – 'ACTIVO' | 'DADO DE BAJA' | ''
            status        int   – 2=activo, 3=dado de baja, 1=desconocido
            error         str
        """
        result = {
            "success": False, "rnc": rnc, "name": "",
            "name_commercial": "", "contributor_type": "",
            "commercial_type": "", "status_raw": "",
            "status": 1, "error": "",
        }
        try:
            vs = _vs(_RNC_PAGE)
            vs["ctl00$cphMain$txtRNCCedula"] = rnc.strip()
            vs["ctl00$cphMain$btnBuscarPorRNC"] = "BUSCAR"

            html = _post(_RNC_PAGE, vs)
            p = _parse(html)
            info = p.span("lblInformacion")

            if info and "no se encuentra" in info.lower():
                result["error"] = (
                    f"RNC/Cédula '{rnc}' no está registrado como contribuyente en la DGII."
                )
                return result

            rnc_val        = _cell_after_bold(html, "RNC").replace("-", "")
            name           = _cell_after_bold(html, "Social")
            name_com       = _cell_after_bold(html, "Comercial")
            contrib_type   = _cell_after_bold(html, "pagos")
            status_raw     = _cell_after_bold(html, "Estado")
            commercial     = _cell_after_bold(html, "Actividad")

            su = status_raw.upper()
            status = 2 if su == "ACTIVO" else 3 if "BAJA" in su else 1

            result.update({
                "success":          bool(rnc_val or name),
                "rnc":              rnc_val or rnc,
                "name":             name,
                "name_commercial":  name_com,
                "contributor_type": contrib_type,
                "commercial_type":  commercial,
                "status_raw":       status_raw,
                "status":           status,
            })
            if not result["success"]:
                result["error"] = "No se encontró información para este RNC/Cédula."

        except urllib.error.URLError as e:
            _logger.exception("DGII connection error RNC=%s", rnc)
            result["error"] = f"Error de conexión con la DGII: {e.reason}"
        except Exception as e:
            _logger.exception("DGII unexpected error RNC=%s", rnc)
            result["error"] = str(e)

        return result

    @staticmethod
    def validate_ncf(rnc_issuer: str, ncf: str,
                     rnc_consumer: str = "", security_code: str = "") -> dict:
        """
        Returns dict:
            success          bool
            rnc_issuer       str
            contributor_name str
            ncf              str
            ncf_type         str
            status           str   – 'VIGENTE' | 'ANULADO' | ...
            due_date         datetime | None
            is_valid         bool
            information      str
            error            str
        """
        result = {
            "success": False, "rnc_issuer": rnc_issuer,
            "contributor_name": "", "ncf": ncf, "ncf_type": "",
            "status": "", "due_date": None,
            "is_valid": False, "information": "", "error": "",
        }
        try:
            vs = _vs(_NCF_PAGE)
            vs["ctl00$cphMain$txtRNC"]          = rnc_issuer.strip()
            vs["ctl00$cphMain$txtNCF"]           = ncf.strip()
            vs["ctl00$cphMain$txtRncComprador"]  = rnc_consumer.strip()
            vs["ctl00$cphMain$txtCodigoSeg"]     = security_code.strip()
            vs["ctl00$cphMain$btnConsultar"]     = "Buscar"

            html = _post(_NCF_PAGE, vs)
            p    = _parse(html)

            prefix = ncf.strip().upper()[0] if ncf.strip() else "B"

            if prefix == "E":
                rnc_v    = p.span("lblrncemisor")
                ncf_v    = p.span("lblencf")
                status   = p.span("lblEstadoFe").upper()
                ncf_type = "e-NCF"
                name     = ""
                raw_date = p.span("lblFechaEmision")
                fmt      = "%Y-%m-%d"
            else:
                name     = _ent(p.span("lblRazonSocial"))
                rnc_v    = p.span("lblRncCedula")
                ncf_v    = p.span("lblNCF")
                ncf_type = _ent(p.span("lblTipoComprobante"))
                status   = p.span("lblEstado").upper()
                raw_date = p.span("lblVigencia")
                fmt      = "%d/%m/%Y"

            due_date = None
            if raw_date:
                try:
                    due_date = datetime.strptime(raw_date.strip(), fmt)
                except ValueError:
                    pass

            info     = p.span("lblInformacion")
            is_valid = (
                "VIGENTE" in status or "ACEPTADO" in status
                or (not status.strip() and info and "es válido" in info.lower())
            )

            result.update({
                "success":          True,
                "rnc_issuer":       rnc_v or rnc_issuer,
                "contributor_name": name,
                "ncf":              ncf_v or ncf,
                "ncf_type":         ncf_type,
                "status":           status,
                "due_date":         due_date,
                "is_valid":         is_valid,
                "information":      info,
            })

        except urllib.error.URLError as e:
            _logger.exception("DGII connection error NCF=%s RNC=%s", ncf, rnc_issuer)
            result["error"] = f"Error de conexión con la DGII: {e.reason}"
        except Exception as e:
            _logger.exception("DGII unexpected error NCF=%s", ncf)
            result["error"] = str(e)

        return result
