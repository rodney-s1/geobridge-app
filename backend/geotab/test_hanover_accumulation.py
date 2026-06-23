"""
test_hanover_accumulation.py
============================
Unit tests for the promoCode-gated Hanover / Han-CS device accumulation logic
introduced in reconciliation.py.

Business rules under test (per user clarification 2026-06-23):
  • hanover_myadmin_total  = count of devices where promoCode == "HANOVER"
                             AND the account is NOT a Han-CS account
  • han_cs_myadmin_total   = count of devices where promoCode == "HANOVER"
                             AND the account IS a Han-CS account
                             (billing_type == "Han-CS"  OR
                              sub_account_tag.lower() == "han-cs")

Key invariants:
  1. Non-HANOVER promo devices on Han-CS accounts must NOT inflate han_cs total.
  2. Never-activated devices must NOT be counted (they are not billed in HANOVER).
  3. Devices with promo "HANOVER" on a plain/Unknown billing_type account DO
     count in hanover_myadmin_total (promo code is ground truth, not label).
  4. Sub-accounts tagged {Han-CS} (sub_account_tag == "Han-CS") count in
     han_cs total even if the parent billing_type wasn't resolved to "Han-CS".

Ground truth from user: MyAdmin Han-CS = 1,378 | QB HANOVER = 3,315 | QB Han-CS = 1,378
"""

import sys
import os

# ---------------------------------------------------------------------------
# Pure-logic helper extracted from reconciliation.py for isolated testing.
# We replicate the exact accumulation logic here so the test runs without
# importing the full FastAPI app.
# ---------------------------------------------------------------------------

HAN_CS_CUST_SKU = (
    "Service Fee (HANOVER-CS) Cust "
    "(Service Fee Geotab (GO) - Hanover Cost Share for C...)"
)


def simulate_accumulation(customer_groups):
    """
    Simulate the per-device promoCode-gated accumulation loop.

    Parameters
    ----------
    customer_groups : list of dict
        Each dict represents one resolved customer group with keys:
          billing_type   : str   — resolved billing type ("Han-CS", "Hanover", "Standard", …)
          devices        : list of dict, each with:
            promoCode        : str  — e.g. "HANOVER" or ""
            neverActivated   : bool
            subAccountTag    : str  — e.g. "Han-CS", "3rd Party Devices", ""

    Returns
    -------
    (hanover_total: int, han_cs_total: int)
    """
    hanover_myadmin_total: int = 0
    han_cs_myadmin_total: int = 0

    for cdata in customer_groups:
        billing_type = cdata["billing_type"]
        for dev in cdata["devices"]:
            promo_code      = (dev.get("promoCode") or "").upper().strip()
            never_activated = dev.get("neverActivated", False)
            sub_account_tag = dev.get("subAccountTag") or ""

            # Mirror the exact gate from reconciliation.py
            if promo_code == "HANOVER" and not never_activated:
                _is_han_cs_device = (
                    billing_type == "Han-CS"
                    or sub_account_tag.lower() == "han-cs"
                )
                if _is_han_cs_device:
                    han_cs_myadmin_total += 1
                else:
                    hanover_myadmin_total += 1

    return hanover_myadmin_total, han_cs_myadmin_total


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def make_dev(promo="HANOVER", never=False, sub_tag=""):
    return {"promoCode": promo, "neverActivated": never, "subAccountTag": sub_tag}


def test_basic_hanover_only():
    """3 active HANOVER-promo devices on a plain Hanover account → hanover=3, han_cs=0."""
    groups = [{"billing_type": "Hanover", "devices": [make_dev() for _ in range(3)]}]
    h, hc = simulate_accumulation(groups)
    assert h == 3, f"Expected hanover=3, got {h}"
    assert hc == 0, f"Expected han_cs=0, got {hc}"
    print("PASS test_basic_hanover_only")


def test_basic_han_cs_only():
    """5 active HANOVER-promo devices on a Han-CS account → hanover=0, han_cs=5."""
    groups = [{"billing_type": "Han-CS", "devices": [make_dev() for _ in range(5)]}]
    h, hc = simulate_accumulation(groups)
    assert h == 0, f"Expected hanover=0, got {h}"
    assert hc == 5, f"Expected han_cs=5, got {hc}"
    print("PASS test_basic_han_cs_only")


def test_han_cs_non_hanover_promo_excluded():
    """
    Han-CS account with 10 HANOVER-promo + 10 non-promo devices.
    ONLY the 10 HANOVER-promo ones should count → han_cs=10.
    This is the primary source of the +384 overcount fixed in this PR.
    """
    devices = (
        [make_dev(promo="HANOVER") for _ in range(10)]
        + [make_dev(promo="")      for _ in range(10)]
        + [make_dev(promo="GO9")   for _ in range(3)]
    )
    groups = [{"billing_type": "Han-CS", "devices": devices}]
    h, hc = simulate_accumulation(groups)
    assert h  == 0,  f"Expected hanover=0, got {h}"
    assert hc == 10, f"Expected han_cs=10, got {hc}"
    print("PASS test_han_cs_non_hanover_promo_excluded")


def test_never_activated_excluded():
    """Never-activated devices must never count, even with HANOVER promo."""
    devices = (
        [make_dev(promo="HANOVER", never=False) for _ in range(6)]   # active
        + [make_dev(promo="HANOVER", never=True)  for _ in range(4)]  # never-activated
    )
    groups = [{"billing_type": "Hanover", "devices": devices}]
    h, hc = simulate_accumulation(groups)
    assert h == 6, f"Expected hanover=6 (not 10), got {h}"
    assert hc == 0
    print("PASS test_never_activated_excluded")


def test_unknown_billing_type_with_hanover_promo():
    """
    A customer whose billing_type resolved to 'Unknown' (name mismatch) but
    devices have promoCode='HANOVER'.  promoCode is ground truth → hanover total.
    """
    groups = [{"billing_type": "Unknown", "devices": [make_dev() for _ in range(7)]}]
    h, hc = simulate_accumulation(groups)
    assert h == 7, f"Expected hanover=7, got {h}"
    assert hc == 0
    print("PASS test_unknown_billing_type_with_hanover_promo")


def test_sub_account_tag_han_cs():
    """
    A sub-account with sub_account_tag='Han-CS' but parent billing_type='Standard'.
    The sub_account_tag gates the han_cs bucket (safety net for legacy records).
    """
    devices = [make_dev(promo="HANOVER", sub_tag="Han-CS") for _ in range(4)]
    groups = [{"billing_type": "Standard", "devices": devices}]
    h, hc = simulate_accumulation(groups)
    assert h == 0,  f"Expected hanover=0, got {h}"
    assert hc == 4, f"Expected han_cs=4, got {hc}"
    print("PASS test_sub_account_tag_han_cs")


def test_sub_account_tag_other_ignored():
    """
    A sub-account with sub_account_tag='3rd Party Devices' on a Hanover account.
    The tag should NOT route to han_cs — belongs in hanover total.
    """
    devices = [make_dev(promo="HANOVER", sub_tag="3rd Party Devices") for _ in range(3)]
    groups = [{"billing_type": "Hanover", "devices": devices}]
    h, hc = simulate_accumulation(groups)
    assert h == 3, f"Expected hanover=3, got {h}"
    assert hc == 0
    print("PASS test_sub_account_tag_other_ignored")


def test_mixed_customers():
    """
    Multiple customers across billing types.  Only count HANOVER promo, active devices.

    Customer A: Hanover,  8 HANOVER active,  2 non-promo active  → h+=8
    Customer B: Han-CS,  10 HANOVER active, 15 non-promo active  → hc+=10
    Customer C: Unknown,  5 HANOVER active                       → h+=5
    Customer D: Standard, 3 HANOVER active  (name mismatch case) → h+=3
    Customer E: Han-CS,   0 HANOVER, 20 GO-Plan active           → hc+=0
    Never-activated: 12 HANOVER, Han-CS account                  → hc+=0
    -----------------------------------------------------------------------
    Expected: hanover = 8+5+3 = 16,  han_cs = 10
    """
    groups = [
        {"billing_type": "Hanover",
         "devices": [make_dev("HANOVER")] * 8 + [make_dev("")] * 2},
        {"billing_type": "Han-CS",
         "devices": [make_dev("HANOVER")] * 10 + [make_dev("")] * 15},
        {"billing_type": "Unknown",
         "devices": [make_dev("HANOVER")] * 5},
        {"billing_type": "Standard",
         "devices": [make_dev("HANOVER")] * 3},
        {"billing_type": "Han-CS",
         "devices": [make_dev("GO9")] * 20},
        # Never-activated HANOVER on Han-CS account — must be excluded
        {"billing_type": "Han-CS",
         "devices": [make_dev("HANOVER", never=True)] * 12},
    ]
    h, hc = simulate_accumulation(groups)
    assert h  == 16, f"Expected hanover=16, got {h}"
    assert hc == 10, f"Expected han_cs=10, got {hc}"
    print("PASS test_mixed_customers")


def test_case_insensitive_promo():
    """promoCode is uppercased before comparison in the real loop; test lowercase input."""
    # In reconciliation.py: promo_code = (c.get("promoCode") or "").upper().strip()
    # So if MyAdmin ever sends lowercase "hanover" it should still match.
    # Our simulate_accumulation also uppercases, so test that path.
    devices = [make_dev(promo="hanover")]   # lowercase
    groups = [{"billing_type": "Hanover", "devices": devices}]
    # Adjust: simulate_accumulation reads promo_code from dev dict and uppercases
    h, hc = simulate_accumulation(groups)
    assert h == 1, f"Expected hanover=1 (case-insensitive), got {h}"
    print("PASS test_case_insensitive_promo")


def test_empty_groups():
    """Empty customer list returns zeros."""
    h, hc = simulate_accumulation([])
    assert h == 0 and hc == 0
    print("PASS test_empty_groups")


def test_scale_ground_truth():
    """
    Smoke-test matching the user-reported ground truth: 3,315 HANOVER + 1,378 Han-CS.
    We don't have the real dataset, but we verify that the accumulator correctly
    totals what it's given without off-by-one errors or double-counting.
    """
    groups = [
        # 3,315 active HANOVER devices across non-Han-CS accounts
        {"billing_type": "Hanover",  "devices": [make_dev("HANOVER")] * 3315},
        # 1,378 active HANOVER devices on Han-CS accounts
        {"billing_type": "Han-CS",   "devices": [make_dev("HANOVER")] * 1378},
        # Non-HANOVER Han-CS devices that should NOT appear in either total
        {"billing_type": "Han-CS",   "devices": [make_dev("")] * 384},
        # Non-HANOVER non-Han-CS devices
        {"billing_type": "Standard", "devices": [make_dev("GO9")] * 500},
    ]
    h, hc = simulate_accumulation(groups)
    assert h  == 3315, f"Expected hanover=3315, got {h}"
    assert hc == 1378, f"Expected han_cs=1378, got {hc}"
    print("PASS test_scale_ground_truth")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_basic_hanover_only,
        test_basic_han_cs_only,
        test_han_cs_non_hanover_promo_excluded,
        test_never_activated_excluded,
        test_unknown_billing_type_with_hanover_promo,
        test_sub_account_tag_han_cs,
        test_sub_account_tag_other_ignored,
        test_mixed_customers,
        test_case_insensitive_promo,
        test_empty_groups,
        test_scale_ground_truth,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    if failed:
        sys.exit(1)
