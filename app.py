import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np
import json
import time
import os

st.set_page_config(page_title="India RE Reverse Auction", layout="wide")

# Poll every 2 seconds to synchronize across all browser tabs
st_autorefresh(interval=2000, key="global_sync")

STATE_FILE = "game_state.json"
ADMIN_KEY = "admin2026"

# ==========================================
# 1. CENTRAL DISK-BASED STATE ENGINE
# ==========================================
def init_default_state():
    return {
        "game_id": 1,
        "phase": "LOBBY",  # LOBBY, PHASE_1, PHASE_2, LIVE_RA, SETTLEMENT
        "tender_capacity": 0.0,
        "ra_start_timestamp": 0.0,
        "last_bid_timestamp": 0.0,
        "teams": {}
    }

def load_game():
    if not os.path.exists(STATE_FILE):
        state = init_default_state()
        save_game(state)
        return state
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        time.sleep(0.05)
        with open(STATE_FILE, "r") as f:
            return json.load(f)

def save_game(state):
    temp_file = STATE_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(temp_file, STATE_FILE)

game = load_game()

# ==========================================
# 2. PERSISTENT LOGIN RECOVERY (URL-BACKED)
# ==========================================
url_team = st.query_params.get("team")
if url_team:
    st.session_state["team_name"] = url_team
elif "team_name" in st.session_state and st.session_state["team_name"]:
    st.query_params["team"] = st.session_state["team_name"]

# ==========================================
# 3. FACILITATOR AUTHENTICATION
# ==========================================
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False

if st.query_params.get("role") == "admin":
    st.session_state["is_admin"] = True

# ==========================================
# 4. IRR MATRIX ENGINE & STRICT BOUNDS
# ==========================================
@st.cache_data
def load_irr_engine():
    with open('irr_matrix.json', 'r') as f:
        data = json.load(f)
    return data

irr_data = load_irr_engine()

MIN_TARIFF = float(min(irr_data['tariffs']))
MAX_TARIFF = float(max(irr_data['tariffs']))
MIN_CAP = float(min(irr_data['capacities']))
MAX_CAP = float(max(irr_data['capacities']))

def lookup_base_irr(tariff: float, capacity: float) -> float:
    if capacity <= 0:
        return 0.0
    
    # Strict boundary guard: return 0.0 if inputs fall outside matrix limits
    if capacity < MIN_CAP or capacity > MAX_CAP:
        return 0.0
    if tariff < MIN_TARIFF or tariff > MAX_TARIFF:
        return 0.0

    caps = irr_data['capacities']
    tariffs = irr_data['tariffs']
    
    cap_idx = min(range(len(caps)), key=lambda i: abs(caps[i] - capacity))
    tariff_idx = min(range(len(tariffs)), key=lambda i: abs(tariffs[i] - tariff))
    return float(irr_data['grid'][tariff_idx][cap_idx])

# ==========================================
# 5. GAME TRANSITIONS & SETTLEMENT
# ==========================================
def advance_game_phase():
    state = load_game()
    phase = state["phase"]
    
    if phase == "LOBBY":
        state["phase"] = "PHASE_1"
    elif phase == "PHASE_1":
        total_mw = sum(t["bid_mw"] for t in state["teams"].values())
        state["tender_capacity"] = min(1500.0, 0.80 * total_mw)
        state["phase"] = "PHASE_2"
    elif phase == "PHASE_2":
        # H1 Elimination: highest ceiling tariff eliminated; ties to later timestamp
        sorted_teams = sorted(
            state["teams"].values(),
            key=lambda t: (-t["ceiling_tariff"], t["timestamp"])
        )
        if sorted_teams:
            eliminated = sorted_teams[0]["name"]
            state["teams"][eliminated]["qualified"] = False
        state["phase"] = "LIVE_RA"
        now = time.time()
        state["ra_start_timestamp"] = now  # 10-minute hard cap anchor
        state["last_bid_timestamp"] = now  # 60-second inactivity anchor
    elif phase == "LIVE_RA":
        finalize_settlement(state)
        
    save_game(state)

def finalize_settlement(state):
    state["phase"] = "SETTLEMENT"
    remaining_cap = state["tender_capacity"]
    
    active = [t for t in state["teams"].values() if t["qualified"]]
    active.sort(key=lambda t: (t["current_tariff"], t["timestamp"]))
    
    for team in active:
        awarded = min(team["bid_mw"], max(0.0, remaining_cap))
        remaining_cap -= awarded
        team["awarded_mw"] = awarded
        
        bonus_land = int(np.random.randint(50, 150))
        team["bonus_land"] = bonus_land
        total_land = 100.0 + bonus_land
        team["total_land"] = total_land
        
        if awarded > 0:
            base = lookup_base_irr(team["current_tariff"], awarded)
            land_deficit = max(0.0, awarded - total_land)
            unawarded = max(0.0, team["bid_mw"] - awarded)
            
            land_pen = land_deficit * 0.0001
            unawarded_pen = unawarded * 0.0001
            
            final_irr = base - land_pen - unawarded_pen
            team["base_irr"] = base
            team["land_pen_bps"] = int(round(land_deficit * 1.0))
            team["unawarded_pen_bps"] = int(round(unawarded * 1.0))
            team["final_irr"] = final_irr
            team["meets_hurdle"] = bool(final_irr >= 0.14)
        else:
            team["base_irr"] = 0.0
            team["land_pen_bps"] = 0
            team["unawarded_pen_bps"] = int(round(team["bid_mw"] * 1.0))
            team["final_irr"] = 0.0
            team["meets_hurdle"] = False

# DUAL TIMERS CHECK (Inactivity: 60s OR Absolute Hard Cap: 600s)
if game["phase"] == "LIVE_RA" and game.get("ra_start_timestamp", 0) > 0:
    now = time.time()
    total_elapsed = now - game["ra_start_timestamp"]
    inactive_elapsed = now - game["last_bid_timestamp"]
    
    if inactive_elapsed >= 60.0 or total_elapsed >= 600.0:
        state = load_game()
        if state["phase"] == "LIVE_RA":
            finalize_settlement(state)
            save_game(state)
            game = state

# ==========================================
# 6. FACILITATOR SIDEBAR
# ==========================================
st.sidebar.title("Access Control")

if not st.session_state["is_admin"]:
    with st.sidebar.expander("Facilitator Login"):
        entered_key = st.text_input("Enter Admin Key", type="password")
        if st.button("Authenticate"):
            if entered_key == ADMIN_KEY:
                st.session_state["is_admin"] = True
                st.sidebar.success("Logged in as Facilitator")
                st.rerun()
            else:
                st.sidebar.error("Invalid key")
else:
    st.sidebar.success("Logged in as Facilitator")
    if st.sidebar.button("Logout Facilitator"):
        st.session_state["is_admin"] = False
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.subheader("Facilitator Controls")
    st.sidebar.write(f"**Current Phase:** `{game['phase']}` (Game #{game['game_id']})")
    
    total_registered = len(game["teams"])
    p1_submitted = sum(1 for t in game["teams"].values() if t["bid_mw"] >= MIN_CAP)
    p2_submitted = sum(1 for t in game["teams"].values() if t["ceiling_tariff"] >= MIN_TARIFF)
    
    if game["phase"] == "PHASE_1":
        st.sidebar.info(f"Phase 1 Submissions: {p1_submitted}/{total_registered}")
    elif game["phase"] == "PHASE_2":
        st.sidebar.info(f"Phase 2 Submissions: {p2_submitted}/{total_registered}")

    if st.sidebar.button("Advance Phase / Execute Transition", type="primary"):
        advance_game_phase()
        st.rerun()

    if st.sidebar.button("Start New Game / Reset All"):
        fresh = init_default_state()
        fresh["game_id"] = game["game_id"] + 1
        save_game(fresh)
        st.sidebar.success("Game reset. All data cleared.")
        st.rerun()

# ==========================================
# 7. MAIN TABS INTERFACE
# ==========================================
st.title("India RE Reverse Auction Simulator")

tab_game, tab_matrix = st.tabs(["🎮 Auction Arena", "📈 Financial Model & IRR Lookup"])

with tab_matrix:
    st.subheader("Base IRR Reference Matrix & Calculator")
    st.info(f"Allowed Tariff Range: **{MIN_TARIFF:.2f}** to **{MAX_TARIFF:.2f} INR/kWh** | Capacity Range: **{int(MIN_CAP)}** to **{int(MAX_CAP)} MW**")
    
    c_m1, c_m2, c_m3 = st.columns(3)
    calc_mw = c_m1.number_input("Test Capacity (MW)", min_value=int(MIN_CAP), max_value=int(MAX_CAP), step=10, value=int(MIN_CAP))
    calc_tariff = c_m2.number_input("Test Tariff (INR/kWh)", min_value=MIN_TARIFF, max_value=MAX_TARIFF, step=0.01, value=MIN_TARIFF, format="%.2f")
    calc_res = lookup_base_irr(calc_tariff, calc_mw)
    c_m3.metric("Corresponding Base IRR", f"{calc_res * 100:.2f}%")
    
    st.markdown("---")
    st.write("**Full Reference Grid (Tariffs vs MW Capacities):**")
    
    matrix_df = pd.DataFrame(
        [[f"{val * 100:.2f}%" for val in row] for row in irr_data['grid']],
        index=[f"{t:.2f}" for t in irr_data['tariffs']],
        columns=[f"{int(c)} MW" for c in irr_data['capacities']]
    )
    matrix_df.index.name = "Tariff (INR/kWh)"
    st.dataframe(matrix_df, use_container_width=True)

with tab_game:
    col_left, col_right = st.columns([1, 1])

    # --- LEFT COLUMN: TEAM PORTAL ---
    with col_left:
        st.subheader("Your Team Portal")

        if st.session_state["is_admin"]:
            st.info("You are the Facilitator. Use the sidebar controls to advance rounds or reset.")
        else:
            current_team_name = st.session_state.get("team_name")

            if not current_team_name:
                with st.form("login_form"):
                    name_input = st.text_input("Company Name:").strip()
                    if st.form_submit_button("Join Game"):
                        if name_input:
                            st.session_state["team_name"] = name_input
                            st.query_params["team"] = name_input
                            
                            state = load_game()
                            if name_input not in state["teams"]:
                                state["teams"][name_input] = {
                                    "name": name_input,
                                    "bid_mw": 0,
                                    "ceiling_tariff": 0.0,
                                    "current_tariff": 0.0,
                                    "timestamp": time.time(),
                                    "qualified": True,
                                    "awarded_mw": 0,
                                    "bonus_land": 0,
                                    "total_land": 100,
                                    "base_irr": 0.0,
                                    "land_pen_bps": 0,
                                    "unawarded_pen_bps": 0,
                                    "final_irr": 0.0,
                                    "meets_hurdle": False
                                }
                                save_game(state)
                            st.rerun()
            else:
                st.success(f"Logged in as: **{current_team_name}**")
                
                state = load_game()
                if current_team_name not in state["teams"]:
                    state["teams"][current_team_name] = {
                        "name": current_team_name,
                        "bid_mw": 0,
                        "ceiling_tariff": 0.0,
                        "current_tariff": 0.0,
                        "timestamp": time.time(),
                        "qualified": True,
                        "awarded_mw": 0,
                        "bonus_land": 0,
                        "total_land": 100,
                        "base_irr": 0.0,
                        "land_pen_bps": 0,
                        "unawarded_pen_bps": 0,
                        "final_irr": 0.0,
                        "meets_hurdle": False
                    }
                    save_game(state)

                my_data = state["teams"][current_team_name]

                if not my_data.get("qualified", True):
                    st.error("Your company was eliminated under the H1 Ceiling Rule.")
                else:
                    # Envelope I Submission (Capacity Limited)
                    if game["phase"] == "PHASE_1":
                        with st.form("env1_form"):
                            st.write("**Envelope I: Technical Bid (Confidential)**")
                            default_mw = my_data.get("bid_mw") if my_data.get("bid_mw", 0) >= MIN_CAP else MIN_CAP
                            mw = st.number_input(f"Bidding Capacity (MW) [{int(MIN_CAP)}-{int(MAX_CAP)} MW in increments of 10]", min_value=int(MIN_CAP), max_value=int(MAX_CAP), step=10, value=int(default_mw))
                            if st.form_submit_button("Submit Capacity"):
                                mw_val = int(mw)
                                if not (MIN_CAP <= mw_val <= MAX_CAP):
                                    st.error(f"Capacity must be between {int(MIN_CAP)} MW and {int(MAX_CAP)} MW.")
                                elif mw_val % 10 != 0:
                                    st.error("Capacity must be submitted in increments of 10 MW.")
                                else:
                                    s = load_game()
                                    s["teams"][current_team_name]["bid_mw"] = mw_val
                                    save_game(s)
                                    st.success(f"Submitted: {mw_val} MW")
                                    st.rerun()

                    # Envelope II Submission (Tariff Limited)
                    elif game["phase"] == "PHASE_2":
                        with st.form("env2_form"):
                            st.write("**Envelope II: Financial Bid (Confidential)**")
                            st.caption(f"Allowed Ceiling Tariff Range: **{MIN_TARIFF:.2f}** to **{MAX_TARIFF:.2f} INR/kWh**")
                            default_t = my_data.get("ceiling_tariff") if my_data.get("ceiling_tariff", 0.0) >= MIN_TARIFF else MAX_TARIFF
                            t_val = st.number_input("Ceiling Tariff (INR/kWh)", min_value=MIN_TARIFF, max_value=MAX_TARIFF, step=0.01, value=float(default_t), format="%.2f")
                            if st.form_submit_button("Submit Ceiling Tariff"):
                                if MIN_TARIFF <= t_val <= MAX_TARIFF:
                                    s = load_game()
                                    s["teams"][current_team_name]["ceiling_tariff"] = float(t_val)
                                    s["teams"][current_team_name]["current_tariff"] = float(t_val)
                                    s["teams"][current_team_name]["timestamp"] = time.time()
                                    save_game(s)
                                    st.success(f"Submitted Ceiling Tariff: {t_val:.2f}")
                                    st.rerun()
                                else:
                                    st.error(f"Tariff must be between {MIN_TARIFF:.2f} and {MAX_TARIFF:.2f} INR/kWh.")

                    # Phase 3: Live Reverse Auction (Tariff Limited + Undercut Validation)
                    elif game["phase"] == "LIVE_RA":
                        current_t = my_data.get('current_tariff', MAX_TARIFF)
                        st.write(f"Your Active Tariff: **{current_t:.2f} INR/kWh**")
                        st.caption(f"Auction Tariff Floor (Lower Limit): **{MIN_TARIFF:.2f} INR/kWh**")
                        
                        with st.form("bid_form"):
                            suggested_bid = max(MIN_TARIFF, round(current_t - 0.01, 2))
                            new_bid = st.number_input(
                                "Submit Undercut Tariff (INR/kWh)",
                                min_value=MIN_TARIFF,
                                max_value=float(current_t),
                                step=0.01,
                                value=float(suggested_bid),
                                format="%.2f"
                            )
                            if st.form_submit_button("Submit Bid"):
                                if new_bid < MIN_TARIFF:
                                    st.error(f"Bid rejected: Tariff cannot go below the minimum limit of {MIN_TARIFF:.2f} INR/kWh.")
                                elif new_bid >= current_t:
                                    st.error("Bid must be strictly lower than your current active tariff.")
                                else:
                                    s = load_game()
                                    s["teams"][current_team_name]["current_tariff"] = float(new_bid)
                                    s["teams"][current_team_name]["timestamp"] = time.time()
                                    s["last_bid_timestamp"] = time.time()  # Reset 60s ticker
                                    save_game(s)
                                    st.success(f"Undercut bid of {new_bid:.2f} accepted.")
                                    st.rerun()

                    elif game["phase"] == "LOBBY":
                        st.info("Waiting for the Facilitator to start Envelope I...")

                    # Phase 4: Final Settlement Breakdown Card
                    elif game["phase"] == "SETTLEMENT":
                        st.markdown("### 📊 Your Final Settlement Breakdown")
                        awarded = my_data.get("awarded_mw", 0)
                        bonus = my_data.get("bonus_land", 0)
                        total_land = my_data.get("total_land", 100 + bonus)
                        base_irr = my_data.get("base_irr", 0.0)
                        final_irr = my_data.get("final_irr", 0.0)
                        hurdle = my_data.get("meets_hurdle", False)

                        land_deficit_mw = max(0, awarded - total_land)
                        unawarded_mw = max(0, my_data.get("bid_mw", 0) - awarded)
                        
                        land_pen = my_data.get("land_pen_bps") or int(round(land_deficit_mw * 1.0))
                        unawarded_pen = my_data.get("unawarded_pen_bps") or int(round(unawarded_mw * 1.0))

                        c1, c2 = st.columns(2)
                        c1.metric("Bid Capacity", f"{my_data.get('bid_mw', 0)} MW")
                        c2.metric("Awarded Capacity", f"{awarded} MW")

                        st.markdown("---")
                        st.markdown(f"**Land Allocation:**")
                        st.write(f"• Base Starting Land: `100 MW`")
                        st.write(f"• 🎲 Random Bonus Land Awarded: `+{bonus} MW`")
                        st.write(f"• **Total Land Available:** `{total_land} MW`")

                        st.markdown("---")
                        st.markdown(f"**IRR & Basis Point Adjustments:**")
                        st.write(f"• Base Model IRR: `{base_irr * 100:.2f}%`")
                        st.write(f"• Land Shortage Penalty: `-{land_pen} bps` (-{land_pen/100:.2f}%)")
                        st.write(f"• Unawarded MW Penalty: `-{unawarded_pen} bps` (-{unawarded_pen/100:.2f}%)")
                        
                        st.markdown(f"### Net Final IRR: `{final_irr * 100:.2f}%`")
                        if hurdle:
                            st.success("🏆 Hurdle Achieved: Qualified with >= 14.00% IRR!")
                        else:
                            st.error("❌ Hurdle Missed: Net IRR is below 14.00%.")

    # --- RIGHT COLUMN: LEADERBOARD & STATUS ---
    with col_right:
        st.subheader("Tender Status & Standings")
        
        if game["tender_capacity"] > 0:
            st.info(f"**Effective Tender Volume:** {game['tender_capacity']:.1f} MW")
        
        # DUAL TIMERS DISPLAY IN LIVE_RA
        if game["phase"] == "LIVE_RA" and game.get("ra_start_timestamp", 0) > 0:
            now = time.time()
            inactivity_left = max(0, int(60 - (now - game.get("last_bid_timestamp", now))))
            auction_left = max(0, int(600 - (now - game.get("ra_start_timestamp", now))))
            
            c_t1, c_t2 = st.columns(2)
            c_t1.metric("Inactivity Countdown", f"{inactivity_left}s", help="Resets to 60s on every lower bid")
            c_t2.metric("Auction Hard Cap", f"{auction_left // 60}m {auction_left % 60}s", help="Absolute 10-minute round cap")

        # 1. PARTICIPANT VIEW IN PHASES 1 & 2 (Confidential)
        if not st.session_state["is_admin"] and game["phase"] in ["LOBBY", "PHASE_1", "PHASE_2"]:
            st.markdown("### Submission Status")
            if current_team_name:
                my_team = game["teams"].get(current_team_name, {})
                c1, c2 = st.columns(2)
                c1.metric("Envelope I (MW)", f"{my_team.get('bid_mw', 0)} MW" if my_team.get('bid_mw', 0) > 0 else "Pending")
                c2.metric("Envelope II (Ceiling)", f"{my_team.get('ceiling_tariff', 0.0):.2f}" if my_team.get('ceiling_tariff', 0.0) > 0 else "Pending")
                st.info("🔒 Confidential Bidding: Competitor bids and merit rankings remain hidden until Phase 3 (Live Reverse Auction).")
            else:
                st.info("🔒 Log in to view your company's submission status.")

        # 2. FACILITATOR VIEW IN PHASES 1 & 2 (Live Readiness Audit)
        elif st.session_state["is_admin"] and game["phase"] in ["LOBBY", "PHASE_1", "PHASE_2"]:
            st.markdown(f"### Facilitator Audit Tape ({game['phase']})")
            audit_records = []
            for t in game["teams"].values():
                p1_status = "✅ Submitted" if t["bid_mw"] >= MIN_CAP else "⏳ Pending"
                p2_status = "✅ Submitted" if t["ceiling_tariff"] >= MIN_TARIFF else "⏳ Pending"
                
                audit_records.append({
                    "Company": t["name"],
                    "Bid (MW)": f"{t['bid_mw']} MW" if t["bid_mw"] > 0 else "--",
                    "Ceiling Tariff": f"{t['ceiling_tariff']:.2f}" if t["ceiling_tariff"] > 0 else "--",
                    "Env I Status": p1_status,
                    "Env II Status": p2_status,
                })
            
            if audit_records:
                st.dataframe(pd.DataFrame(audit_records), use_container_width=True, hide_index=True)
            else:
                st.info("No teams have joined the lobby yet.")

        # 3. LIVE REVERSE AUCTION TAPE (Public to all)
        elif game["phase"] == "LIVE_RA":
            teams_list = list(game["teams"].values())
            teams_list.sort(key=lambda x: (x["current_tariff"], x["timestamp"]))
            records = []
            for idx, t in enumerate(teams_list):
                status = "Active" if t["qualified"] else "Eliminated (H1)"
                prefix = f"L{idx+1} - " if t["qualified"] else ""
                records.append({
                    "Rank / Company": f"{prefix}{t['name']}",
                    "Bid (MW)": t["bid_mw"],
                    "Current Tariff": f"{t['current_tariff']:.2f}" if t["current_tariff"] > 0 else "--",
                    "Status": status
                })
            st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)

        # 4. FINAL SETTLEMENT AUDIT (Public to all)
        elif game["phase"] == "SETTLEMENT":
            teams_list = list(game["teams"].values())
            teams_list.sort(key=lambda x: -x["final_irr"])
            records = []
            for t in teams_list:
                status = "🏆 Pass (>=14%)" if t["meets_hurdle"] else "❌ Fail (<14%)"
                if not t["qualified"]:
                    status = "Eliminated (H1)"
                
                awarded = t.get("awarded_mw", 0)
                tot_land = t.get("total_land", 100 + t.get("bonus_land", 0))
                l_pen = t.get("land_pen_bps") or max(0, awarded - tot_land)
                u_pen = t.get("unawarded_pen_bps") or max(0, t.get("bid_mw", 0) - awarded)

                records.append({
                    "Company": t["name"],
                    "Awarded (MW)": awarded,
                    "Total Land (MW)": tot_land,
                    "Final Tariff": f"{t['current_tariff']:.2f}",
                    "Base IRR": f"{t.get('base_irr', 0.0)*100:.2f}%",
                    "Land Pen": f"-{l_pen} bps",
                    "Unawarded Pen": f"-{u_pen} bps",
                    "Net Final IRR": f"{t.get('final_irr', 0.0)*100:.2f}%",
                    "Verdict": status
                })
            st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
