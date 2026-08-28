"""
qb_connection.py — Shared QuickBooks Desktop (qbXML) connection helper.

WINDOWS-ONLY. Uses the QBXMLRP2.RequestProcessor COM object — a single,
version-independent ProgID that accepts/returns raw qbXML strings — rather
than a versioned QBFCxx.QBSessionManager typelib, so we never need to know
which QBFC version is registered for a given SDK install (confirmed working
against QuickBooks Desktop Enterprise 2024 / SDK 17.0 via qb_test_connection.py).

This module is imported LAZILY by API routes (never at module load time) so
that running the backend on a non-Windows machine (e.g. this repo being
edited/tested from the Linux sandbox) never crashes on import — pywin32
simply isn't installed there. Every public function here raises a clear
QBConnectionError instead of letting an ImportError/COM error leak out.

USAGE
─────
    from geotab import qb_connection

    try:
        customers = qb_connection.fetch_customers_from_qb()
    except qb_connection.QBConnectionError as exc:
        # surface exc as an HTTP 502 or similar — this is an expected,
        # user-facing failure mode (QB not open, permission not granted,
        # not running on Windows, etc.), not a bug.
        ...

CONNECTION LIFECYCLE
─────────────────────
Every call opens a fresh connection + session and closes it when done (via
the `qb_session()` context manager). QBFC/qbXML connections are cheap to
open against an already-running local QuickBooks Desktop instance — there's
no need to keep a persistent connection alive between requests, and doing so
would risk leaving a dangling session if GeoBridge crashes or restarts.

FIRST-CONNECTION PERMISSION GRANT
───────────────────────────────────
The first time ANY app connects to a company file, QuickBooks pops up a
dialog asking whether to allow it — this must be accepted with "Yes,
always; allow access even if QuickBooks is not running" while logged in as
Admin, in single-user mode. This is a one-time, per-machine, per-company-file
grant. See qb_test_connection.py's docstring for troubleshooting.
"""

import xml.etree.ElementTree as ET
from contextlib import contextmanager
from typing import Iterator, Optional

APP_NAME = "GeoBridge"

# qbXML version to negotiate. 13.0 is broadly supported by all QB Desktop
# versions since ~2013 and is sufficient for the Customer/Item/Invoice
# requests we use — no need to request the newest version the SDK supports.
QBXML_VERSION = "13.0"


class QBConnectionError(Exception):
    """
    Raised whenever GeoBridge can't connect to / communicate with
    QuickBooks Desktop. This is always an EXPECTED, user-facing failure
    mode (QB not running, permission not granted, wrong OS, request
    rejected by QB) — callers should catch this and surface a clear
    message rather than a raw 500.
    """


def _win32com_client():
    try:
        import win32com.client
        return win32com.client
    except ImportError as exc:
        raise QBConnectionError(
            "QuickBooks Desktop integration requires Windows + the pywin32 "
            "package, which isn't available in this environment."
        ) from exc


class QBSession:
    """A single open connection + session against local QuickBooks Desktop."""

    def __init__(self, processor, ticket):
        self._processor = processor
        self._ticket = ticket

    def send(self, request_xml: str) -> str:
        """Send a raw qbXML request string, return the raw qbXML response string."""
        try:
            return self._processor.ProcessRequest(self._ticket, request_xml)
        except Exception as exc:
            raise QBConnectionError(f"QuickBooks request failed: {exc}") from exc


@contextmanager
def qb_session(app_name: str = APP_NAME) -> Iterator[QBSession]:
    """
    Open a connection + session against whatever company file QuickBooks
    Desktop currently has open locally, yield a QBSession, and always clean
    up (EndSession/CloseConnection) on exit — even on error.
    """
    win32com_client = _win32com_client()
    processor = win32com_client.Dispatch("QBXMLRP2.RequestProcessor")

    try:
        # "" app-file path = whatever company file QB currently has open.
        # 1 = ctLocalQBD (connect to a locally-installed QuickBooks instance;
        # QB itself handles the network hop to a multi-user server file).
        processor.OpenConnection2("", app_name, 1)
    except Exception as exc:
        raise QBConnectionError(
            f"Could not open a connection to QuickBooks Desktop: {exc}"
        ) from exc

    try:
        # "" file path = the currently-open company file.
        # 2 = omDontCare (accept whatever single/multi-user mode QB is
        # already running in).
        ticket = processor.BeginSession("", 2)
    except Exception as exc:
        try:
            processor.CloseConnection()
        except Exception:
            pass
        raise QBConnectionError(
            "Could not start a QuickBooks session. Make sure QuickBooks "
            "Desktop is open with the company file loaded, and that "
            "GeoBridge has been granted permission to access it (first "
            "connection requires being logged into QuickBooks as Admin in "
            "single-user mode — see Edit > Preferences > Integrated "
            f"Applications afterward to confirm). Details: {exc}"
        ) from exc

    session = QBSession(processor, ticket)
    try:
        yield session
    finally:
        try:
            processor.EndSession(ticket)
        except Exception:
            pass
        try:
            processor.CloseConnection()
        except Exception:
            pass


def _qbxml_header() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<?qbxml version="{QBXML_VERSION}"?>\n'
    )


def _text(el: Optional[ET.Element], path: str, default: str = "") -> str:
    """Find a child element by tag path and return its text, or default."""
    if el is None:
        return default
    child = el.find(path)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _ref_full_name(el: Optional[ET.Element], ref_tag: str) -> str:
    """Read the FullName of a *Ref element (e.g. JobTypeRef/FullName)."""
    if el is None:
        return ""
    ref = el.find(ref_tag)
    if ref is None:
        return ""
    return _text(ref, "FullName")


def test_connection() -> dict:
    """
    Lightweight connectivity check — opens a session, queries 1 customer,
    closes. Used by a health-check endpoint so the UI can show a clear
    connected/not-connected status without doing a full customer pull.
    """
    request_xml = (
        _qbxml_header() +
        '<QBXML><QBXMLMsgsRq onError="stopOnError">'
        '<CustomerQueryRq requestID="1"><MaxReturned>1</MaxReturned></CustomerQueryRq>'
        '</QBXMLMsgsRq></QBXML>'
    )
    with qb_session() as session:
        response_xml = session.send(request_xml)
    root = ET.fromstring(response_xml)
    rs = root.find(".//CustomerQueryRs")
    status_code = rs.get("statusCode") if rs is not None else "?"
    status_msg = rs.get("statusMessage") if rs is not None else "unknown"
    return {"connected": True, "statusCode": status_code, "statusMessage": status_msg}


# ── Customers ──────────────────────────────────────────────────────────────
def fetch_customers_from_qb() -> list[dict]:
    """
    Query ALL customers/jobs from QuickBooks and return them as a list of
    dicts shaped identically to what customers.py's CSV import
    (POST /api/customers/import-qb) produces, so both paths can feed the
    same qb_customers.json cache via the same merge logic.

    Fields returned per record (raw — normalize()/company-id matching is
    the caller's job, same division of responsibility as the CSV path):
      name          — child-only name if this is a "Parent:Job", else FullName
      qbFullName    — the original QB "Parent:Job" FullName, unmodified
      accountNo     — AccountNumber
      jobType       — JobTypeRef/FullName (drives billingType via map_billing_type)
      terms         — TermsRef/FullName
      qbClass       — ClassRef/FullName
      balance       — TotalBalance (falls back to Balance)
      billTo1..5    — BillAddress Addr1..Addr5 (raw address lines; NOTE this
                      may not exactly match the CSV export's "Bill to N"
                      column semantics — see docstring caveat below)

    ADDRESS CAVEAT: The CSV import's "Bill to 1".."Bill to 5" columns come
    from QuickBooks' own CSV export format, which — per existing comments
    in customers.py — repeats the customer name on line 1. QBFC's
    <BillAddress><Addr1>..<Addr5> fields are qbXML's own address-line
    representation and have NOT yet been verified to follow the identical
    convention (the one live record checked so far, "** Estimates", is an
    auto-generated placeholder with non-representative data). Spot-check a
    real customer's rendered invoice address after the first live refresh.

    No pagination/MaxReturned limit is applied — qbXML returns all matching
    records in one response when MaxReturned is omitted, which is fine for
    a customer list in the hundreds.
    """
    request_xml = (
        _qbxml_header() +
        '<QBXML><QBXMLMsgsRq onError="stopOnError">'
        '<CustomerQueryRq requestID="1"><OwnerID>0</OwnerID></CustomerQueryRq>'
        '</QBXMLMsgsRq></QBXML>'
    )

    with qb_session() as session:
        response_xml = session.send(request_xml)

    root = ET.fromstring(response_xml)
    rs = root.find(".//CustomerQueryRs")
    if rs is None:
        raise QBConnectionError("Unexpected QuickBooks response: no CustomerQueryRs found.")

    status_code = rs.get("statusCode", "-1")
    if status_code not in ("0", "1"):  # 1 = "no matching records" — not an error
        raise QBConnectionError(
            f"QuickBooks CustomerQuery failed (status {status_code}): "
            f"{rs.get('statusMessage', 'unknown error')}"
        )

    results = []
    for cust in rs.findall("CustomerRet"):
        full_name = _text(cust, "FullName") or _text(cust, "Name")
        if not full_name:
            continue

        # Same "Parent:Child" -> child-only convention as the CSV import,
        # so lookups against MyAdmin company names keep working identically
        # regardless of which import path populated the cache.
        qb_full_name = full_name
        name = full_name.rsplit(":", 1)[-1].strip() if ":" in full_name else full_name

        bill_addr = cust.find("BillAddress")
        balance = _text(cust, "TotalBalance") or _text(cust, "Balance") or "0"

        results.append({
            "name":        name,
            "qbFullName":  qb_full_name,
            "accountNo":   _text(cust, "AccountNumber"),
            "jobType":     _ref_full_name(cust, "JobTypeRef"),
            "terms":       _ref_full_name(cust, "TermsRef"),
            "qbClass":     _ref_full_name(cust, "ClassRef"),
            "balance":     balance,
            "billTo1":     _text(bill_addr, "Addr1"),
            "billTo2":     _text(bill_addr, "Addr2"),
            "billTo3":     _text(bill_addr, "Addr3"),
            "billTo4":     _text(bill_addr, "Addr4"),
            "billTo5":     _text(bill_addr, "Addr5"),
        })

    return results


# ── Items (catalog) ──────────────────────────────────────────────────────────
# Item types QuickBooks can return from an ItemQueryRq — each has a
# different *Ret wrapper tag. We care about the ones that can plausibly be
# billed on an invoice line (Service, Inventory, NonInventory, OtherCharge).
_ITEM_RET_TAGS = [
    "ItemServiceRet",
    "ItemInventoryRet",
    "ItemNonInventoryRet",
    "ItemOtherChargeRet",
]


def fetch_items_from_qb() -> list[dict]:
    """
    Query ALL billable items from QuickBooks (Service, Inventory,
    NonInventory, OtherCharge types) and return them shaped close to
    sku_catalog.json's entry format: {skuKey, fullPath, defaultPrice, desc}.

    skuKey / fullPath are both set to the item's FullName (QB item code
    format) since sku_catalog.json's own fullPath field is documented
    elsewhere as "(QB item code format)" — i.e. the two are meant to be the
    same string for items that map 1:1. Category is left blank; matching
    that up with GeoBridge's existing category taxonomy is a follow-up step,
    not attempted here.

    NOT YET WIRED to any endpoint or the sku_catalog.json cache — this is
    the read primitive only, added alongside fetch_customers_from_qb() for
    the later Item-List preflight check. Building the merge-into-catalog
    logic is a separate follow-up once the preflight check design is final.
    """
    request_xml = (
        _qbxml_header() +
        '<QBXML><QBXMLMsgsRq onError="stopOnError">'
        '<ItemQueryRq requestID="1"><OwnerID>0</OwnerID></ItemQueryRq>'
        '</QBXMLMsgsRq></QBXML>'
    )

    with qb_session() as session:
        response_xml = session.send(request_xml)

    root = ET.fromstring(response_xml)
    rs = root.find(".//ItemQueryRs")
    if rs is None:
        raise QBConnectionError("Unexpected QuickBooks response: no ItemQueryRs found.")

    status_code = rs.get("statusCode", "-1")
    if status_code not in ("0", "1"):
        raise QBConnectionError(
            f"QuickBooks ItemQuery failed (status {status_code}): "
            f"{rs.get('statusMessage', 'unknown error')}"
        )

    results = []
    for tag in _ITEM_RET_TAGS:
        for item in rs.findall(tag):
            full_name = _text(item, "FullName") or _text(item, "Name")
            if not full_name:
                continue
            sales = item.find("SalesAndPurchase")
            sales_or_purchase = item.find("SalesOrPurchase")
            price = (
                _text(sales, "SalesPrice") or _text(sales_or_purchase, "Price") or "0"
            )
            desc = (
                _text(sales, "SalesDesc") or _text(sales_or_purchase, "Desc") or ""
            )
            results.append({
                "skuKey":       full_name,
                "fullPath":     full_name,
                "defaultPrice": price,
                "desc":         desc,
                "isActive":     _text(item, "IsActive", "true") == "true",
            })

    return results
