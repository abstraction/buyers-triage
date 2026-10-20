import subprocess
import json
import os
import time
import tempfile
import shutil
import urllib.request
import urllib.error
import sys
import argparse
import math
from pathlib import Path
from datetime import date

# Budget Configuration
TARGET_BUDGET = 55000
CEILING_BUDGET = 70000

TARGET_BUDGET_STR = f"₹{TARGET_BUDGET:,}"
TARGET_BUDGET_SHORT = f"₹{TARGET_BUDGET // 1000}k"
CEILING_BUDGET_STR = f"₹{CEILING_BUDGET:,}"
CEILING_BUDGET_SHORT = f"₹{CEILING_BUDGET // 1000}k"


# Project paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RESULTS_DIR = PROJECT_ROOT / "results"

STATE_FILE = DATA_DIR / "state_ledger.json"
OUTPUT_FILE = RESULTS_DIR / "ULTIMATE_RANKING.md"
PHONE_LIST_FILE = CONFIG_DIR / "phone_list.txt"

# Phase 2 validation constants
REQUIRED_PHASE2_FIELDS = [
    "price_inr", "soc_exact_model", "ram_type", "storage_speed",
    "primary_sensor", "auxiliary_lenses", "gcam_aux_access",
    "dontkillmyapp_score_out_of_5", "tasker_survival_without_root",
    "proximity_sensor_type", "earpiece_loudness_and_mic_clarity"
]

INVALID_VALUES = {"not found", "n/a", "unknown", "tbd", "0", "", "none"}


def current_date_str():
    return date.today().strftime("%-d %B %Y")


def load_phones():
    if not PHONE_LIST_FILE.exists():
        print(f"[!] Error: Phone list file not found at {PHONE_LIST_FILE}")
        sys.exit(1)

    phones = []
    with open(PHONE_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("*") or line.startswith("-"):
                line = line[1:].strip()
            if line:
                phones.append(line)

    if not phones:
        print(f"[!] Error: No phones found in {PHONE_LIST_FILE}")
        sys.exit(1)

    return phones


PHONES = load_phones()


def check_internet(host='http://clients3.google.com/generate_204', timeout=3):
    try:
        urllib.request.urlopen(host, timeout=timeout)
        return True
    except Exception:
        return False


def wait_for_internet():
    if check_internet():
        return
    print("  [!] No internet connection detected. Waiting...", flush=True)
    while not check_internet():
        time.sleep(5)
    print("  [+] Internet connection restored.", flush=True)


def check_dependencies():
    if not shutil.which("agy"):
        print("[!] Error: 'agy' CLI tool is not installed or not in PATH.")
        sys.exit(1)


import threading

state_lock = threading.Lock()

def load_state():
    with state_lock:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    # Ensure new phones are added if the list changed
                    for p in PHONES:
                        if p not in state:
                            state[p] = {"phase1": "PENDING", "phase2": "PENDING", "data": {}}
                    return state
            except json.JSONDecodeError:
                print("[!] State file corrupted. Starting fresh.")
        return {phone: {"phase1": "PENDING", "phase2": "PENDING", "data": {}} for phone in PHONES}

def save_state(state):
    with state_lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Atomic write to prevent corruption
        fd, temp_path = tempfile.mkstemp(dir=DATA_DIR)
        with os.fdopen(fd, 'w') as f:
            json.dump(state, f, indent=2)
        shutil.move(temp_path, STATE_FILE)


def run_agy(prompt, model, log_prefix=""):
    # Add a generous 30-minute timeout for deep searches
    cmd = ["agy", "-p", prompt, "--model", model, "--dangerously-skip-permissions", "--print-timeout", "1800s"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace')
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if e.returncode in (-2, 2, 130):
            raise KeyboardInterrupt
        err_msg = e.stderr.strip() if e.stderr else str(e)
        print(f"{log_prefix}  [!] CLI Error: {err_msg}")
        
        # Handle quota / rate limits gracefully
        err_lower = err_msg.lower()
        if "quota" in err_lower or "429" in err_lower or "rate limit" in err_lower:
            print(f"{log_prefix}  [!] API Quota or Rate Limit exceeded. Sleeping for 15 minutes before next attempt...", flush=True)
            time.sleep(900)
            
        return None
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"{log_prefix}  [!] OS Error: {str(e)}")
        return None


def extract_json(text):
    """Extract outermost JSON object using bracket-depth matching."""
    if not text:
        return None
    text = text.strip()
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        if depth == 0:
            return text[start:i + 1].strip()
    return None


def query_with_retry(prompt, model, max_retries=3, log_prefix=""):
    base_delay = 5
    max_delay = 60

    for attempt in range(1, max_retries + 1):
        wait_for_internet()
        print(f"{log_prefix}  -> Attempt {attempt}/{max_retries}...", flush=True)
        raw = run_agy(prompt, model, log_prefix)

        if raw is not None:
            if not raw.strip():
                print(f"{log_prefix}  [!] AI returned empty output. Retrying...")
            else:
                extracted = extract_json(raw)
                if extracted:
                    try:
                        data = json.loads(extracted)
                        return data
                    except json.JSONDecodeError as err:
                        print(f"{log_prefix}  [!] AI returned malformed JSON ({err}). Retrying...")
                else:
                    preview = raw.strip().replace('\n', ' ')[:100]
                    print(f"{log_prefix}  [!] No JSON object found in AI response ('{preview}...'). Retrying...")
        else:
            print(f"{log_prefix}  [!] Execution failed. Retrying...")

        if attempt < max_retries:
            sleep_time = min(base_delay * (2 ** (attempt - 1)), max_delay)
            print(f"{log_prefix}  [⏳] Error. Exponential backoff: waiting {sleep_time}s...", flush=True)
            time.sleep(sleep_time)

    return None


def partition_balanced(items, num_bins):
    """Partition a list into num_bins balanced chunks differing in size by at most 1."""
    if num_bins <= 0:
        return []
    base = len(items) // num_bins
    rem = len(items) % num_bins
    chunks = []
    idx = 0
    for i in range(num_bins):
        size = base + (1 if i < rem else 0)
        chunks.append(items[idx:idx + size])
        idx += size
    return chunks

def validate_phase2_data(data):
    """Validate that Phase 2 data contains real values, not placeholders.
    Returns (is_valid, list_of_problems)."""
    problems = []
    for field in REQUIRED_PHASE2_FIELDS:
        if field not in data:
            problems.append(f"Missing field: {field}")
            continue
        val = str(data[field]).strip().lower()
        if val in INVALID_VALUES:
            problems.append(f"Invalid value for {field}: '{data[field]}'")
    if data.get("price_inr", 0) == 0:
        problems.append("price_inr is 0 or missing")
    return (len(problems) == 0, problems)


def check_cross_phase_consistency(state, survivors):
    """Flag phones where Phase 2 data contradicts Phase 1 verdicts."""
    issues = []
    for phone in survivors:
        p2 = state[phone]["data"].get("phase2_result", {})
        soc = str(p2.get("soc_exact_model", "")).lower()

        # Phase 1 required Snapdragon. Does Phase 2 confirm it?
        if "mediatek" in soc or "dimensity" in soc or "exynos" in soc:
            issues.append(
                f"{phone}: Phase 1 passed Snapdragon gate, but Phase 2 "
                f"extracted SoC '{p2.get('soc_exact_model')}' (non-Snapdragon)"
            )

        # Price sanity check
        price = p2.get("price_inr", 0)
        if price > 0 and (price < 15000 or price > CEILING_BUDGET):
            issues.append(f"{phone}: Price ₹{price} is outside any reasonable range for this evaluation")

    return issues


def print_status(state):
    """Print a summary table of all phone states and exit."""
    print(f"\n{'Phone':<35} {'Phase 1':<12} {'Phase 2':<12} {'P1 Status':<8} {'SoC (P2)':<30}")
    print("-" * 100)
    for phone in PHONES:
        s = state.get(phone, {})
        p1 = s.get("phase1", "?")
        p2 = s.get("phase2", "?")
        p1_status = s.get("data", {}).get("phase1_result", {}).get("status", "-")
        soc = s.get("data", {}).get("phase2_result", {}).get("soc_exact_model", "-")
        print(f"{phone:<35} {p1:<12} {p2:<12} {p1_status:<8} {soc:<30}")

    # Summary counts
    survivors = [p for p in PHONES if state.get(p, {}).get("data", {}).get("phase1_result", {}).get("status") == "PASS"]
    p2_done = [p for p in survivors if state.get(p, {}).get("phase2") == "COMPLETED"]
    p2_err = [p for p in survivors if state.get(p, {}).get("phase2") == "ERROR"]
    p2_pend = [p for p in survivors if state.get(p, {}).get("phase2") == "PENDING"]

    print(f"\nPhase 1 survivors: {len(survivors)}")
    print(f"Phase 2 completed: {len(p2_done)}, errors: {len(p2_err)}, pending: {len(p2_pend)}")

    if p2_done:
        issues = check_cross_phase_consistency(state, p2_done)
        if issues:
            print(f"\n⚠️  Cross-phase consistency issues ({len(issues)}):")
            for iss in issues:
                print(f"  - {iss}")

        for phone in p2_done:
            p2_data = state[phone]["data"].get("phase2_result", {})
            valid, problems = validate_phase2_data(p2_data)
            if not valid:
                print(f"\n⚠️  Validation issues for {phone}:")
                for prob in problems:
                    print(f"  - {prob}")


def parse_args():
    parser = argparse.ArgumentParser(description="Phone Research Pipeline")
    parser.add_argument("--reset-phase2", metavar="PHONE",
                        help="Reset a specific phone's Phase 2 status to PENDING for re-extraction")
    parser.add_argument("--reset-all-errors", action="store_true",
                        help="Reset all ERROR states to PENDING for retry")
    parser.add_argument("--reset-triage", action="store_true",
                        help="Clear Phase 2.7 triage scorecard cache and re-evaluate.")
    parser.add_argument("--status", action="store_true",
                        help="Print current state summary and exit")
    return parser.parse_args()


def main():
    args = parse_args()
    check_dependencies()
    state = load_state()

    # Handle --status
    if args.status:
        print_status(state)
        return

    # Handle --reset-triage
    if args.reset_triage:
        if "_meta" in state and "triage" in state["_meta"]:
            state["_meta"].pop("triage", None)
            save_state(state)
            print("[✓] Reset Phase 2.7 triage cache.")
        else:
            print("[i] No triage cache found to reset.")
        return

    # Handle --reset-phase2
    if args.reset_phase2:
        phone = args.reset_phase2
        if phone not in state:
            print(f"[!] Phone '{phone}' not found in state ledger.")
            sys.exit(1)
        state[phone]["phase2"] = "PENDING"
        state[phone]["data"].pop("phase2_result", None)
        state[phone]["data"].pop("phase2_meta", None)
        save_state(state)
        print(f"[✓] Reset Phase 2 for '{phone}' to PENDING.")
        return

    # Handle --reset-all-errors
    if args.reset_all_errors:
        count = 0
        for phone in PHONES:
            if state[phone].get("phase1") == "ERROR":
                state[phone]["phase1"] = "PENDING"
                state[phone]["data"].pop("phase1_result", None)
                state[phone]["data"].pop("phase1_meta", None)
                count += 1
            if state[phone].get("phase2") == "ERROR":
                state[phone]["phase2"] = "PENDING"
                state[phone]["data"].pop("phase2_result", None)
                state[phone]["data"].pop("phase2_meta", None)
                count += 1
        save_state(state)
        print(f"[✓] Reset {count} ERROR state(s) to PENDING.")
        return

    # ========================================
    # PHASE 1: THE REALITY GATE
    # ========================================
    print("========================================")
    print("PHASE 1: THE REALITY GATE")
    print("========================================")

    today = current_date_str()
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def run_phase1(phone):
        if state[phone]["phase1"] == "COMPLETED":
            print(f"[SKIP] {phone} already completed Phase 1.")
            return

        log_prefix = f"[{phone}]"
        print(f"\n{log_prefix} Phase 1")
        prompt = f"""CURRENT DATE: {today}.
Search the web (using search_web or similar tools) to verify the commercial reality of the smartphone '{phone}'.
Check these hard gates:
1. Is this phone actually released and commercially available in India right now? (Rumored/Vaporware = FAIL)
2. Is it a China-only SKU that lacks Indian carrier bands/Play Integrity? (Yes = FAIL)
3. Does it have a Snapdragon or Google Tensor SoC? (MediaTek or Exynos = FAIL. Note: Snapdragon 7-series is acceptable if it meets the other criteria, do not fail it just for not being 8-series).
4. Is the base variant equipped with UFS 3.1 or 4.0? (UFS 2.2 = FAIL)
5. Is the current MRP/retail price of this phone {CEILING_BUDGET_STR} INR or lower? (We are targeting a {TARGET_BUDGET_SHORT} budget for upcoming sales. A 35% discount is the absolute maximum realistic drop, so phones currently up to {CEILING_BUDGET_SHORT} are allowed as aspirational targets. Phones > {CEILING_BUDGET_SHORT} = FAIL).

Output ONLY a valid JSON object without markdown formatting or conversational filler:
{{
  "status": "PASS or FAIL",
  "soc_found": "Exact SoC name (e.g. Snapdragon 7s Gen 4) or NONE",
  "storage_found": "e.g. UFS 3.1 or UFS 2.2",
  "reason": "brief reason"
}}"""

        data = query_with_retry(prompt, "Gemini 3.1 Pro (High)", log_prefix=log_prefix)

        if data:
            # Enforce Python-side validation against AI hallucinations
            soc_found = str(data.get("soc_found", "")).lower()
            if data.get("status") == "PASS":
                if "snapdragon" not in soc_found and "qualcomm" not in soc_found and "tensor" not in soc_found:
                    print(f"{log_prefix}  [!] HALLUCINATION CAUGHT: AI passed the phone but found SoC '{data.get('soc_found')}'. Overriding to FAIL.")
                    data["status"] = "FAIL"
                    data["reason"] = f"Override: Model extracted invalid SoC ({data.get('soc_found')})"

            with state_lock:
                state[phone]["phase1"] = "COMPLETED"
                state[phone]["data"]["phase1_result"] = data
                state[phone]["data"]["phase1_meta"] = {
                    "model": "Gemini 3.1 Pro (High)",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
            save_state(state)

            if data.get("status") == "PASS":
                print(f"{log_prefix}  ✅ PASSED: {phone} (SoC: {data.get('soc_found')})")
            else:
                print(f"{log_prefix}  ❌ FAILED: {phone} - {data.get('reason')}")
        else:
            with state_lock:
                state[phone]["phase1"] = "ERROR"
            save_state(state)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(run_phase1, phone) for phone in PHONES]
        for future in as_completed(futures):
            future.result()

    # ========================================
    # PHASE 2: STRICT DATA EXTRACTION
    # ========================================
    print("\n========================================")
    print("PHASE 2: STRICT DATA EXTRACTION")
    print("========================================")

    survivors = [p for p in PHONES if state[p]["phase1"] == "COMPLETED" and state[p]["data"].get("phase1_result", {}).get("status") == "PASS"]

    def run_phase2(phone):
        if state[phone]["phase2"] == "COMPLETED":
            print(f"[SKIP] {phone} already completed Phase 2.")
            return

        log_prefix = f"[{phone}]"
        print(f"\n{log_prefix} Phase 2")
        
        # Ground Phase 2 with Phase 1's facts to prevent hallucination drift
        p1_soc = state[phone]["data"].get("phase1_result", {}).get("soc_found", "Snapdragon")
        p1_storage = state[phone]["data"].get("phase1_result", {}).get("storage_found", "UFS")
        
        prompt = f"""CURRENT DATE: {today}.
SEARCH THE WEB (using search_web or similar tools) for technical engineering data on the '{phone}'. Look for XDA teardowns, GSMarena optical specs, and dontkillmyapp.com ratings.

CRITICAL FACT-CHECK: Phase 1 already verified this phone uses a {p1_soc} processor and {p1_storage} storage. Do NOT hallucinate a different manufacturer (e.g. MediaTek) for this device.

Output ONLY a JSON object extracting these exact fields without conversational filler:
{{
  "price_inr": <approximate integer price in INR>,
  "soc_exact_model": "...",
  "ram_type": "e.g., LPDDR5X",
  "storage_speed": "e.g., UFS 3.1",
  "primary_sensor": "e.g., Sony IMX890, OIS",
  "auxiliary_lenses": "e.g., 8MP ultrawide, no telephoto",
  "gcam_aux_access": "WORKING / REQUIRES_ROOT / BLOCKED",
  "dontkillmyapp_score_out_of_5": <integer>,
  "tasker_survival_without_root": "PASS / FAIL (does stock OS kill foreground services without root?)",
  "proximity_sensor_type": "PHYSICAL / VIRTUAL",
  "earpiece_loudness_and_mic_clarity": "..."
}}"""

        data = query_with_retry(prompt, "Gemini 3.1 Pro (High)", log_prefix=log_prefix)
        missing_probs = []

        if data:
            valid, problems = validate_phase2_data(data)
            
            # TWO-PASS DEEP SEARCH: If missing data, trigger a targeted follow-up query
            if not valid:
                missing_probs = [p for p in problems if "Invalid value" in p or "Missing" in p]
                if missing_probs:
                    print(f"{log_prefix}  [!] Initial extraction missed fields: {', '.join(missing_probs)}")
                    print(f"{log_prefix}  [!] Initiating Targeted Deep Search (Pass 2)...")
                    follow_up_prompt = prompt + f"\n\nCRITICAL ERROR IN PREVIOUS ATTEMPT: You failed to find valid data for these specific fields: {missing_probs}.\nYou MUST actively browse the web (Smartprix, 91mobiles, Reddit) to find them. Do NOT return 'Not found'."
                    
                    data2 = query_with_retry(follow_up_prompt, "Gemini 3.1 Pro (High)", log_prefix=log_prefix)
                    if data2:
                        # Merge the newly found fields
                        for k, v in data2.items():
                            if str(v).strip().lower() not in INVALID_VALUES:
                                data[k] = v
                        
                        # Re-validate after merge
                        valid, problems = validate_phase2_data(data)
                        
            if valid:
                with state_lock:
                    state[phone]["phase2"] = "COMPLETED"
                    state[phone]["data"]["phase2_result"] = data
                    state[phone]["data"]["phase2_meta"] = {
                        "model": "Gemini 3.1 Pro (High)",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "passes_used": 2 if missing_probs else 1
                    }
                save_state(state)
                print(f"{log_prefix}  ✅ Strict Data gathered for {phone}.")
            else:
                print(f"{log_prefix}  [!] Data validation failed for {phone} even after deep search:")
                for prob in problems:
                    print(f"{log_prefix}      - {prob}")
                with state_lock:
                    state[phone]["phase2"] = "ERROR"
                save_state(state)
        else:
            with state_lock:
                state[phone]["phase2"] = "ERROR"
            save_state(state)

        time.sleep(3)  # Anti-rate limit

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(run_phase2, phone) for phone in survivors]
        for future in as_completed(futures):
            future.result()

    # ========================================
    # PHASE 3: MULTI-AGENT BOARD DEBATE
    # ========================================
    print("\n========================================")
    print("PHASE 3: MULTI-AGENT BOARD DEBATE")
    print("========================================")

    if not survivors:
        print("[!] No phones survived Phase 1.")
        return

    # Strict Gate: Block Phase 3 until ALL survivors have completed Phase 2
    missing_phase2 = [p for p in survivors if state[p]["phase2"] != "COMPLETED"]
    if missing_phase2:
        print(f"\n⛔ HALTING BEFORE PHASE 3: Missing Phase 2 data for {len(missing_phase2)} surviving phone(s):")
        for p in missing_phase2:
            print(f"  - {p} (status: {state[p]['phase2']})")
        print("\nPhase 3 will NOT run until all surviving phones have completed Phase 2 data extraction.")
        print("Please re-run the pipeline to retry fetching data for the missing phones.")
        return

    # Cross-phase consistency check
    consistency_issues = check_cross_phase_consistency(state, survivors)
    if consistency_issues:
        print(f"\n⛔ HALTING BEFORE PHASE 3: Cross-phase data contradictions detected:")
        for iss in consistency_issues:
            print(f"  - {iss}")
        print("\nUse --reset-phase2 'Phone Name' to clear bad data and re-run.")
        return

    # ========================================
    # PHASE 2.5: DETERMINISTIC HARD PRUNING
    # ========================================
    pruned_survivors = []
    pruned_reasons = {}
    for p in survivors:
        res = state[p]["data"]["phase2_result"]
        price = res.get("price_inr", 0)

        # Hard Gate 1: Over-budget phones that cannot realistically hit sale targets
        if price > CEILING_BUDGET:
            pruned_reasons[p] = f"Price ₹{price:,} exceeds ceiling budget of {CEILING_BUDGET_STR}"
            print(f"  [PRUNE] {p}: {pruned_reasons[p]}")
            continue

        # Hard Gate 2: GCam completely blocked or requires root (user's #1 priority, but no root allowed)
        gcam = str(res.get("gcam_aux_access", "")).upper()
        if "BLOCKED" in gcam or "REQUIRES" in gcam:
            pruned_reasons[p] = f"GCam access is BLOCKED or REQUIRES ROOT (Camera API restriction)"
            print(f"  [PRUNE] {p}: {pruned_reasons[p]}")
            continue

        pruned_survivors.append(p)

    print(f"\n  Phase 2.5: {len(survivors)} → {len(pruned_survivors)} survivors "
          f"({len(pruned_reasons)} pruned)")

    TRIAGE_THRESHOLD = 40  # Phones scoring below this are excluded from Phase 3

    def calculate_triage_score(phone, phase2_data):
        score = 0

        # === Dimension 1: Camera Hardware & GCam (0-50 pts) ===
        gcam = str(phase2_data.get("gcam_aux_access", "")).upper()
        if "Pixel" in phone:
            score += 25
        elif "WORKING" in gcam:
            score += 25
        elif "REQUIRES_ROOT" in gcam:
            score += 0

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

    # ========================================
    # PHASE 2.7: DETERMINISTIC TRIAGE SCORECARD
    # ========================================
    print("\n========================================")
    print("PHASE 2.7: DETERMINISTIC TRIAGE SCORECARD")
    print("========================================")

    triage_results = []
    for p in pruned_survivors:
        p2 = state[p]["data"]["phase2_result"]
        score = calculate_triage_score(p, p2)
        triage_results.append((score, p))

    triage_results.sort(key=lambda x: (-x[0], x[1]))

    print(f"\n{'Rank':<5} {'Score':<7} {'Phone':<30} {'Price':<10} {'Prox':<10} {'GCam':<15} {'BgKill':<7}")
    print("-" * 90)
    for idx, (score, p) in enumerate(triage_results, 1):
        p2 = state[p]["data"]["phase2_result"]
        price = p2.get("price_inr", 0)
        prox = p2.get("proximity_sensor_type", "?")
        gcam = p2.get("gcam_aux_access", "?")
        tasker = p2.get("tasker_survival_without_root", "?")
        marker = " ✓" if score >= TRIAGE_THRESHOLD else " ✗"
        print(f"{idx:<5} {score:<7} {p:<30} ₹{price:<9} {prox:<10} {gcam:<15} {tasker:<7}{marker}")

    shortlisted = [(s, p) for s, p in triage_results if s >= TRIAGE_THRESHOLD]
    excluded = [(s, p) for s, p in triage_results if s < TRIAGE_THRESHOLD]

    print(f"\n✅ {len(shortlisted)} phones shortlisted for Phase 3 (score ≥ {TRIAGE_THRESHOLD})")
    if excluded:
        print(f"   {len(excluded)} phones below triage threshold (published in reference appendix)")

    if "_meta" not in state:
        state["_meta"] = {}
        
    state["_meta"]["triage"] = {
        "shortlisted": [{"phone": p, "score": s} for s, p in shortlisted],
        "excluded": [{"phone": p, "score": s} for s, p in excluded],
        "pruned": pruned_reasons
    }
    save_state(state)

    pruned_survivors = [p for _, p in shortlisted]
    final_data = [
        {
            "phone": p,
            "deep_dive": state[p]["data"]["phase2_result"]
        }
        for p in pruned_survivors
    ]

    print(f"\nRunning final Phase 3 Grand Finals across {len(final_data)} contenders...")
    final_payload = json.dumps(final_data, indent=2)

    final_prompt = f"""CURRENT DATE: {today}.

### MISSION
You are running a Simulated Multi-Agent Technical Review Board.
Three engineering specialists are ranking {len(final_data)} shortlisted Android smartphones
for an Indian power user.

BUDGET CONTEXT: The user's flexible budget is around ₹35,000-55,000.
Baseline prices are provided for reference. The user will check live sale prices
on Amazon/Flipkart themselves. Do NOT speculate on sale discounts or assume future prices.
Evaluate phones on pure technical merit. The user will make their own price/value tradeoff
at purchase time.

CRITICAL CONSTRAINTS (MUST OBEY):
1. NO ROOT / NO CUSTOM ROMS: The user relies on Indian banking apps.
2. OS FREEDOM & FLUIDITY: Value UI fluidity and speed of daily use (NOT gaming).
   Aggressive background management (OxygenOS/ColorOS) is a drawback, but
   do NOT disqualify OnePlus/BBK devices. Weigh the trade-offs fairly.
3. OFFICIAL WARRANTY ONLY.
4. RAW CAMERA HARDWARE & GCAM/MOTIONCAM COMPATIBILITY ARE THE ABSOLUTE PRIMARY CRITERIA.
   The user wants to take exceptional photos and videos, bypassing poor default apps.
5. HARDWARE VS SOFTWARE BALANCE: No phone gets a free pass based on brand name.
6. PROXIMITY SENSOR: Virtual proximity sensors are acceptable and no longer heavily penalized.
7. EARPIECE LOUDNESS & MIC CLARITY: Important for basic usability.
8. BACKGROUND SERVICE SURVIVAL: The user runs Anki, Obsidian, Syncthing, and BuzzKill. Aggressive
   background killing (especially of BuzzKill, which the user pays for) is a significant penalty, even though camera hardware is still the primary driver.

### THE REVIEW BOARD ROSTER
1. Dr. Alex Vance (OS Freedom & Background Services Evaluator)
2. Marcus Reed (Computational Photography Architect & Board Lead)
3. Priya Sharma (Audio & Hardware Usability Specialist)

### THE RESEARCH DATA
{final_payload}

### EXECUTION PROTOCOL
SECTION 1: THE DELIBERATION TRANSCRIPT
Produce a verbatim transcript of the board's meeting across ALL {len(final_data)} finalists.
- Round 1: Head-to-Head Technical Clashes across camera physics, OS freedom, and
  hardware reliability (proximity sensors, thermal throttling, earpiece/mic quality).
- Round 2: Background Service Stress Test (Anki/Obsidian/Syncthing/BuzzKill survival,
  battery optimization aggressiveness).
- Round 3: Final Consensus Ranking.

SECTION 2: MASTER RANKING TABLE
Produce a single flat ranked table. No artificial roles (no "Dream/Fallback/Safe Bet").
Just rank every phone from best to worst with a one-line technical justification.

Format:
| Rank | Phone | Baseline Price | Key Strengths | Key Compromise |
|------|-------|----------------|---------------|----------------|

SECTION 3: TOP 5 DEEP DIVES
For the top 5 ranked phones, write a detailed 3-paragraph technical autopsy covering:
(a) Camera system analysis (sensor hardware, GCam/MotionCam potential, aux lenses) - PRIMARY FOCUS
(b) OS and background service behavior (Anki/Obsidian/Syncthing/BuzzKill reliability, UI fluidity, bloatware)
(c) Audio and sensor hardware (earpiece volume, mic clarity)
"""
    wait_for_internet()
    final_ranking = run_agy(final_prompt, "Gemini 3.1 Pro (High)")

    if final_ranking:
        triage_data = state.get("_meta", {}).get("triage", {})

        appendix = "\n\n## Reference: Full Triage Scorecard (All Evaluated Phones)\n\n"
        appendix += "| Triage Rank | Phone | Triage Score | Baseline Price | Shortlisted? |\n"
        appendix += "| :--- | :--- | :--- | :--- | :--- |\n"

        all_triage = triage_data.get("shortlisted", []) + triage_data.get("excluded", [])
        all_triage.sort(key=lambda x: -x["score"])
        for idx, entry in enumerate(all_triage, 1):
            p = entry["phone"]
            s = entry["score"]
            price = state[p]["data"]["phase2_result"].get("price_inr", 0)
            status = "✅ Yes" if s >= TRIAGE_THRESHOLD else "❌ No"
            appendix += f"| {idx} | **{p}** | {s}/100 | ₹{price:,} | {status} |\n"

        pruned = triage_data.get("pruned", {})
        if pruned:
            appendix += "\n### Pruned Before Scoring\n\n"
            appendix += "| Phone | Reason |\n| :--- | :--- |\n"
            for p, reason in sorted(pruned.items()):
                appendix += f"| **{p}** | {reason} |\n"

        final_ranking += appendix
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(final_ranking)
        print(f"\n🏆 DONE! Check {OUTPUT_FILE}")
    else:
        print("Failed to generate final ranking.")


if __name__ == "__main__":
    main()
