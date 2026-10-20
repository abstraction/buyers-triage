import json

with open("data/state_ledger.json") as f:
    state = json.load(f)

def calculate_triage_score(phone, phase2_data):
    score = 0

    # === Dimension 1: Camera Hardware & GCam (0-50 pts) ===
    # IGNORING the "blocks gcam" hallucination: Treat BLOCKED or REQUIRES_ROOT as WORKING for these phones
    # actually, wait, the prompt says "ignoring the stale 'blocks gcam' hallucination". So we should give them the WORKING score.
    gcam = str(phase2_data.get("gcam_aux_access", "")).upper()
    if "Pixel" in phone:
        score += 25
    else:
        # Treat all as WORKING for this score calculation if we ignore the hallucination?
        # Let's give +25 if WORKING, BLOCKED, or REQUIRES_ROOT
        score += 25

    aux = str(phase2_data.get("auxiliary_lenses", "")).lower()
    if "telephoto" in aux or "periscope" in aux:
        score += 15
    elif "ultrawide" in aux and "8mp" not in aux:
        score += 5
    elif "ultrawide" in aux:
        score += 3
        
    primary = str(phase2_data.get("primary_sensor", "")).lower()
    if "ois" in primary:
        score += 10

    # === Dimension 2: Background Reliability (0-10 pts) ===
    tasker = str(phase2_data.get("tasker_survival_without_root", "")).upper()
    if "PASS" in tasker:
        score += 5
    score += 5

    # === Dimension 3: Physical Hardware Quality (0-15 pts) ===
    prox = str(phase2_data.get("proximity_sensor_type", "")).upper()
    if "PHYSICAL" in prox:
        score += 5

    storage = str(phase2_data.get("storage_speed", "")).upper()
    if "4.0" in storage or "4.1" in storage:
        score += 10
    elif "3.1" in storage:
        score += 7

    # === Dimension 4: SoC Performance Tier (0-25 pts) ===
    soc = str(phase2_data.get("soc_exact_model", "")).lower()
    if any(x in soc for x in ["8 elite", "8 gen 5"]):
        score += 18
    elif any(x in soc for x in ["8 gen 3", "8s gen 4"]):
        score += 15
    elif any(x in soc for x in ["8s gen 3", "8 gen 2", "tensor g4"]):
        score += 12
    elif any(x in soc for x in ["7+ gen 3", "7 gen 4"]):
        score += 9
    elif any(x in soc for x in ["7s gen 4", "7s gen 3"]):
        score += 7

    ram = str(phase2_data.get("ram_type", "")).upper()
    if "5X" in ram:
        score += 7
    elif "5" in ram:
        score += 5

    return score

results = []
for phone, data in state.items():
    if phone.startswith("_"):
        continue
    p2 = data.get("data", {}).get("phase2_result", {})
    if not p2:
        continue
    price = p2.get("price_inr", 0)
    if price > 70000:
        continue
        
    score = calculate_triage_score(phone, p2)
    dkma = p2.get("dontkillmyapp_score_out_of_5", 0)
    
    aux = p2.get("auxiliary_lenses", "").lower()
    has_tele = ("telephoto" in aux or "periscope" in aux) and "no telephoto" not in aux
    tele = "Yes" if has_tele else "No"
    
    results.append((score, dkma, tele, price, phone, p2))

# Sort primarily by Telephoto (Yes > No), then DKMA (descending), then Score (descending)
# "Combine the logic of the tournament (which prioritized telephoto and DKMA) with the raw hardware specs"
results.sort(key=lambda x: (x[2] == "Yes", x[1], x[0]), reverse=True)

print(f"{'Rank':<5} | {'Phone':<25} | {'Score':<5} | {'DKMA':<4} | {'Tele':<4} | {'Price':<7} | {'Original GCam'}")
print("-" * 90)
for i, (score, dkma, tele, price, phone, p2) in enumerate(results[:15], 1):
    gcam = p2.get("gcam_aux_access", "")
    print(f"{i:<5} | {phone:<25} | {score:<5} | {dkma:<4} | {tele:<4} | {price:<7} | {gcam}")
