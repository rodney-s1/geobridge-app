"""
qb_test_connection.py — Throwaway QuickBooks Desktop SDK connection test.

PURPOSE
───────
This is NOT part of the GeoBridge app. It's a standalone diagnostic script to
verify, on Rodney's real machine, that:
  1. The QuickBooks SDK (installed) can actually connect to a running
     QuickBooks Desktop company file via COM automation.
  2. What fields QuickBooks actually returns for a Customer record — in
     particular whether "Job Type", "Terms", and "Class" (used today by the
     manual CSV import in customers.py) are present in the live SDK response.

This uses the QBXMLRP2.RequestProcessor COM object (NOT QBFC/QBSessionManager).
The reason: QBXMLRP2.RequestProcessor is a single, version-independent ProgID
that accepts/returns raw qbXML strings. It sidesteps the need to know exactly
which versioned QBFC type-library ProgID (QBFC13, QBFC15, QBFC17, etc.) is
registered on this machine — RequestProcessor2 negotiates the qbXML version
internally. Since we don't yet know the exact field names QuickBooks Desktop
Enterprise 2024 / SDK 17.0 returns, working in raw XML also means we can just
read the response text directly instead of guessing COM property names ahead
of time.

HOW TO RUN (on the Windows machine, with QuickBooks Desktop open and the
company file loaded):

    cd C:\\dev\\geobridge-app
    .venv\\Scripts\\activate
    pip install pywin32
    python backend\\geotab\\qb_test_connection.py

WHAT WILL HAPPEN
─────────────────
1. The script opens a connection to QuickBooks. The FIRST time you run this,
   QuickBooks will pop up a dialog asking whether to allow "GeoBridge QB Test"
   to access the company file. Choose "Yes, always; allow access even if
   QuickBooks is not running" and click Continue/Done.
2. It sends a qbXML CustomerQueryRq requesting just 1 customer (to keep the
   output short) and prints the raw XML response to the console.
3. It also does the same for an ItemQueryRq (1 item) — useful for later
   confirming the Item List pull can read fields like FullName/Type/Price.
4. It saves both raw XML responses to qb_test_customer_response.xml and
   qb_test_item_response.xml in the current directory so they can be pasted
   back for review.

NOTHING in this script writes/modifies anything in QuickBooks. It only reads.

Safe to delete after use — this file is not imported by any part of the
running GeoBridge app.
"""

import sys

try:
    import win32com.client
except ImportError:
    print("ERROR: pywin32 is not installed. Run: pip install pywin32")
    sys.exit(1)


APP_NAME = "GeoBridge QB Test"


def open_processor():
    """Open a connection + session, return (processor, ticket)."""
    processor = win32com.client.Dispatch("QBXMLRP2.RequestProcessor")
    processor.OpenConnection2("", APP_NAME, 1)  # 1 = qbXMLRPConnectionType.localQBD
    # "" for company file path = whatever company file QuickBooks currently
    # has open. ticket is the session token used on every subsequent call.
    ticket = processor.BeginSession("", 2)  # 2 = qbFileOpenDoNotCare
    return processor, ticket


def close_processor(processor, ticket):
    try:
        processor.EndSession(ticket)
    except Exception as exc:
        print(f"  (warning) EndSession failed: {exc}")
    try:
        processor.CloseConnection()
    except Exception as exc:
        print(f"  (warning) CloseConnection failed: {exc}")


def qbxml_header(version: str = "13.0") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<?qbxml version="{version}"?>\n'
    )


CUSTOMER_QUERY_XML = (
    qbxml_header() +
    "<QBXML>\n"
    "  <QBXMLMsgsRq onError=\"stopOnError\">\n"
    "    <CustomerQueryRq requestID=\"1\">\n"
    "      <MaxReturned>1</MaxReturned>\n"
    "      <OwnerID>0</OwnerID>\n"
    "    </CustomerQueryRq>\n"
    "  </QBXMLMsgsRq>\n"
    "</QBXML>\n"
)

ITEM_QUERY_XML = (
    qbxml_header() +
    "<QBXML>\n"
    "  <QBXMLMsgsRq onError=\"stopOnError\">\n"
    "    <ItemQueryRq requestID=\"1\">\n"
    "      <MaxReturned>1</MaxReturned>\n"
    "      <OwnerID>0</OwnerID>\n"
    "    </ItemQueryRq>\n"
    "  </QBXMLMsgsRq>\n"
    "</QBXML>\n"
)


def run_query(processor, ticket, label: str, request_xml: str, out_file: str):
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print("=" * 70)
    try:
        response_xml = processor.ProcessRequest(ticket, request_xml)
    except Exception as exc:
        print(f"  ERROR sending request: {exc}")
        return

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(response_xml)

    print(response_xml)
    print(f"\n  (saved full response to {out_file})")


def main():
    print(f"Connecting to QuickBooks as '{APP_NAME}'...")
    print("If QuickBooks shows a permission popup, choose 'Yes, always allow'.")

    try:
        processor, ticket = open_processor()
    except Exception as exc:
        print(f"\nFAILED to connect: {exc}")
        print("\nCommon causes:")
        print("  - QuickBooks Desktop isn't running / no company file open")
        print("  - QuickBooks and this script aren't running at the same")
        print("    Windows user privilege level (don't run one as admin and")
        print("    not the other)")
        print("  - The SDK isn't installed, or pywin32 can't see the COM")
        print("    registration (try: python -m win32com.client.makepy)")
        sys.exit(1)

    print("Connected. Session started.")

    try:
        run_query(
            processor, ticket,
            "CUSTOMER — raw qbXML response (1 customer)",
            CUSTOMER_QUERY_XML,
            "qb_test_customer_response.xml",
        )
        run_query(
            processor, ticket,
            "ITEM — raw qbXML response (1 item)",
            ITEM_QUERY_XML,
            "qb_test_item_response.xml",
        )
    finally:
        close_processor(processor, ticket)
        print("\nSession ended, connection closed.")

    print("\nDONE. Please share the two .xml files (or paste their contents)")
    print("so we can confirm field names like JobTypeRef, TermsRef, ClassRef,")
    print("AccountNumber on the customer, and FullName/Type/Price on the item.")


if __name__ == "__main__":
    main()
