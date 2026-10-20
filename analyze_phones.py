import json

with open("data/state_ledger.json") as f:
    state = json.load(f)

CEILING_BUDGET = 70000

print(f"{'Phone':<25} | {'Price':<7} | {'DKMA':<4} | {'GCam':<15} | {'Telephoto':<10} | {'SoC':<20}")
print("-" * 120)

for phone, data in state.items():
    if phone.startswith("_"):
        continue
    
    p2 = data.get("data", {}).get("phase2_result", {})
    if not p2:
        continue
        
    price = p2.get("price_inr", 0)
    if price > CEILING_BUDGET:
        continue
        
    aux = p2.get("auxiliary_lenses", "").lower()
    has_tele = ("telephoto" in aux or "periscope" in aux) and "no telephoto" not in aux
    
    dkma = p2.get("dontkillmyapp_score_out_of_5", 0)
    gcam = p2.get("gcam_aux_access", "")
    soc = p2.get("soc_exact_model", "")
    
    tele_str = "Yes" if has_tele else "No"
    print(f"{phone:<25} | {price:<7} | {dkma:<4} | {gcam:<15} | {tele_str:<10} | {soc:<20}")

