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
            billingPlan      : str  — e.g. "GO", "Pro Mode", "Suspend Mode", ""
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
            billing_plan    = (dev.get("billingPlan") or "").strip()
            never_activated = dev.get("neverActivated", False)
            sub_account_tag = dev.get("subAccountTag") or ""

            # Mirror the exact gate from reconciliation.py:
            # promoCode == "HANOVER", active, AND strictly on the base GO plan.
            if (promo_code == "HANOVER"
                    and not never_activated
                    and billing_plan.upper() == "GO"):
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

def make_dev(promo="HANOVER", never=False, sub_tag="", plan="GO"):
    return {"promoCode": promo, "neverActivated": never, "subAccountTag": sub_tag, "billingPlan": plan}


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
    Han-CS account with 10 HANOVER-promo GO devices + 10 non-promo + 3 GO9-promo.
    ONLY the 10 HANOVER-promo GO-plan ones should count → han_cs=10.
    """
    devices = (
        [make_dev(promo="HANOVER", plan="GO") for _ in range(10)]
        + [make_dev(promo="",       plan="GO") for _ in range(10)]
        + [make_dev(promo="GO9",    plan="GO") for _ in range(3)]
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
    Multiple customers across billing types.  Only count HANOVER promo, active, GO-plan devices.

    Customer A: Hanover,  8 HANOVER GO active,  2 non-promo GO active      → h+=8
    Customer B: Han-CS,  10 HANOVER GO active, 15 non-promo GO active      → hc+=10
    Customer C: Unknown,  5 HANOVER GO active                              → h+=5
    Customer D: Standard, 3 HANOVER GO active  (name mismatch case)        → h+=3
    Customer E: Han-CS,   0 HANOVER, 20 GO-Plan active (wrong promo)       → hc+=0
    Customer F: Han-CS,   4 HANOVER Pro Mode active  (wrong plan)          → hc+=0
    Never-activated: 12 HANOVER GO, Han-CS account                         → hc+=0
    -----------------------------------------------------------------------
    Expected: hanover = 8+5+3 = 16,  han_cs = 10
    """
    groups = [
        {"billing_type": "Hanover",
         "devices": [make_dev("HANOVER", plan="GO")] * 8 + [make_dev("", plan="GO")] * 2},
        {"billing_type": "Han-CS",
         "devices": [make_dev("HANOVER", plan="GO")] * 10 + [make_dev("", plan="GO")] * 15},
        {"billing_type": "Unknown",
         "devices": [make_dev("HANOVER", plan="GO")] * 5},
        {"billing_type": "Standard",
         "devices": [make_dev("HANOVER", plan="GO")] * 3},
        {"billing_type": "Han-CS",
         "devices": [make_dev("GO9", plan="GO")] * 20},
        # HANOVER promo but wrong billing plan — must NOT count
        {"billing_type": "Han-CS",
         "devices": [make_dev("HANOVER", plan="Pro Mode")] * 4},
        # Never-activated HANOVER GO on Han-CS account — must be excluded
        {"billing_type": "Han-CS",
         "devices": [make_dev("HANOVER", never=True, plan="GO")] * 12},
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
    All qualifying devices are HANOVER promo, active, on the base GO plan.
    """
    groups = [
        # 3,315 active HANOVER GO devices across non-Han-CS accounts
        {"billing_type": "Hanover",  "devices": [make_dev("HANOVER", plan="GO")] * 3315},
        # 1,378 active HANOVER GO devices on Han-CS accounts
        {"billing_type": "Han-CS",   "devices": [make_dev("HANOVER", plan="GO")] * 1378},
        # Non-HANOVER Han-CS GO devices — must NOT count
        {"billing_type": "Han-CS",   "devices": [make_dev("", plan="GO")] * 384},
        # Non-HANOVER non-Han-CS devices
        {"billing_type": "Standard", "devices": [make_dev("GO9", plan="GO")] * 500},
    ]
    h, hc = simulate_accumulation(groups)
    assert h  == 3315, f"Expected hanover=3315, got {h}"
    assert hc == 1378, f"Expected han_cs=1378, got {hc}"
    print("PASS test_scale_ground_truth")


def test_go_plan_gate_hanover():
    """HANOVER promo devices NOT on the base GO plan must not count — Hanover side."""
    devices = (
        [make_dev("HANOVER", plan="GO")]           * 5   # qualifying
        + [make_dev("HANOVER", plan="Pro Mode")]   * 3   # wrong plan
        + [make_dev("HANOVER", plan="Suspend Mode")] * 2 # wrong plan
        + [make_dev("HANOVER", plan="GO EXPAND")]  * 1   # wrong plan (not base GO)
        + [make_dev("HANOVER", plan="")]           * 2   # blank plan = never-activated-like, not GO
    )
    groups = [{"billing_type": "Hanover", "devices": devices}]
    h, hc = simulate_accumulation(groups)
    assert h == 5, f"Expected hanover=5 (only base GO), got {h}"
    assert hc == 0
    print("PASS test_go_plan_gate_hanover")


def test_go_plan_gate_han_cs():
    """HANOVER promo devices NOT on the base GO plan must not count — Han-CS side."""
    devices = (
        [make_dev("HANOVER", plan="GO")]           * 7   # qualifying
        + [make_dev("HANOVER", plan="ProPlus Mode")] * 4 # wrong plan
        + [make_dev("HANOVER", plan="Regulatory Mode")] * 2  # wrong plan
    )
    groups = [{"billing_type": "Han-CS", "devices": devices}]
    h, hc = simulate_accumulation(groups)
    assert h  == 0, f"Expected hanover=0, got {h}"
    assert hc == 7, f"Expected han_cs=7 (only base GO), got {hc}"
    print("PASS test_go_plan_gate_han_cs")


def test_go_plan_case_insensitive():
    """billing_plan 'go', 'Go', 'GO' should all qualify (uppercased before compare)."""
    devices = [
        make_dev("HANOVER", plan="go"),
        make_dev("HANOVER", plan="Go"),
        make_dev("HANOVER", plan="GO"),
    ]
    groups = [{"billing_type": "Hanover", "devices": devices}]
    h, hc = simulate_accumulation(groups)
    assert h == 3, f"Expected hanover=3, got {h}"
    print("PASS test_go_plan_case_insensitive")


# ---------------------------------------------------------------------------
# Tests: per-SKU neverActivatedCount correctness
# ---------------------------------------------------------------------------



def simulate_never_activated_by_sku(device_rows):
    """
    Replicate the never_activated_by_sku dict built in reconciliation.py.
    device_rows: list of dicts with 'skuKey' and optional 'neverActivated'.
    Returns {skuKey: count_of_never_activated_on_that_sku}.
    """
    myadmin_by_sku = {}
    never_activated_by_sku = {}
    for row in device_rows:
        sk = row.get("skuKey") or ""
        if sk:
            myadmin_by_sku[sk] = myadmin_by_sku.get(sk, 0) + 1
            if row.get("neverActivated"):
                never_activated_by_sku[sk] = never_activated_by_sku.get(sk, 0) + 1
    return myadmin_by_sku, never_activated_by_sku


def test_per_sku_never_activated_count():
    """
    The Blood Connection scenario (from screenshot):
      - 4 never-activated devices total, all inherited to 'Geotab Service (GO SW-SI2)'
      - Multiple other SKU rows that previously showed '4 never activated' incorrectly.

    After fix: only 'Geotab Service (GO SW-SI2)' should show neverActivatedCount=4.
    All other SKU rows should show neverActivatedCount=0.
    """
    device_rows = [
        # BlueArrow: 13 active
        *[{"skuKey": "BlueArrow Fuel Service", "neverActivated": False}] * 13,
        # GO SW-SI2: 249 active + 4 never-activated (inherited)
        *[{"skuKey": "Geotab Service (GO SW-SI2)", "neverActivated": False}] * 249,
        *[{"skuKey": "Geotab Service (GO SW-SI2)", "neverActivated": True}] * 4,
    ]
    myadmin_by_sku, never_by_sku = simulate_never_activated_by_sku(device_rows)

    assert myadmin_by_sku["BlueArrow Fuel Service"] == 13
    assert myadmin_by_sku["Geotab Service (GO SW-SI2)"] == 253  # 249 + 4

    # KEY ASSERTION: never-activated count is per-SKU, not customer-level total
    assert never_by_sku.get("BlueArrow Fuel Service", 0) == 0, \
        "BlueArrow row should show 0 never-activated, not the customer total"
    assert never_by_sku.get("Geotab Service (GO SW-SI2)", 0) == 4, \
        "GO SW-SI2 row should show 4 (the devices that actually inherited this SKU)"
    print("PASS test_per_sku_never_activated_count")


def test_per_sku_never_activated_split_across_skus():
    """
    Stokes County scenario: 1 never-activated device, 5 SKU rows.
    The 1 never-activated device inherits 'Geotab Service (GO SW-SI2)'.
    Only that SKU row should show neverActivatedCount=1.
    All others should show 0.
    """
    # 20 active on GO SW-SI2, 1 never-activated inherits GO SW-SI2
    # Other SKUs have only QB data (myAdminCount=0)
    device_rows = [
        *[{"skuKey": "Geotab Service (GO SW-SI2)", "neverActivated": False}] * 20,
        {"skuKey": "Geotab Service (GO SW-SI2)", "neverActivated": True},  # 1 inherited
    ]
    _, never_by_sku = simulate_never_activated_by_sku(device_rows)

    assert never_by_sku.get("Geotab Service (GO SW-SI2)", 0) == 1
    # SKUs with no device_rows entries (QB-only) won't appear in never_by_sku at all
    assert never_by_sku.get("GO9 SW", 0) == 0
    assert never_by_sku.get("Geotab Service (GO SW-SI3)", 0) == 0
    assert never_by_sku.get("Geotab Shipping", 0) == 0
    print("PASS test_per_sku_never_activated_split_across_skus")


def test_no_never_activated_devices():
    """Customer with zero never-activated devices: all rows should show 0."""
    device_rows = [
        {"skuKey": "Geotab Service (GO SW-SI2)", "neverActivated": False},
        {"skuKey": "Geotab Service (GO SW-SI2)", "neverActivated": False},
        {"skuKey": "BlueArrow Fuel Service", "neverActivated": False},
    ]
    _, never_by_sku = simulate_never_activated_by_sku(device_rows)
    assert never_by_sku == {}, f"Expected empty dict, got {never_by_sku}"
    print("PASS test_no_never_activated_devices")


# ---------------------------------------------------------------------------
# Runner — must be last so all test functions are defined
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
        test_go_plan_gate_hanover,
        test_go_plan_gate_han_cs,
        test_go_plan_case_insensitive,
        test_per_sku_never_activated_count,
        test_per_sku_never_activated_split_across_skus,
        test_no_never_activated_devices,
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
