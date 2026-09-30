import streamlit as st
import pandas as pd
import numpy as np
import json
import time

st.set_page_config(page_title="India RE Reverse Auction", layout="wide")

# --- 1. SHARED PERSISTENT STATE ---
@st.cache_resource
def get_global_game_state():
    return {
        "phase": "LOBBY",  # LOBBY, PHASE_1, PHASE_2, LIVE_RA, SETTLEMENT
        "tender_capacity": 0.0,
        "last_bid_timestamp": 0.0,
        "teams": {}  # name -> {bid_mw, ceiling_tariff, current_tariff, timestamp, qualified, awarded_mw, final_irr, meets_hurdle}
    }

game = get_global_game_state()

# --- 2. LOAD IRR MATRIX ---
@st.cache_data
def load_irr_engine():
    with open('irr_matrix.json', 'r') as f:
        data = json.load(f)
    return data

irr_data = load_irr_engine()

def lookup_base_irr(tariff: float, capacity: float) -> float:
    if capacity <= 0:
        return 0.0
    caps = irr_data['capacities']
    tariffs = irr_data['tariffs']
    
    cap_idx = min(range(len(caps)), key=lambda i: abs(caps[i] - capacity))
    tariff_idx = min(range(len(tariffs)), key=lambda i: abs(tariffs[i] - tariff))
    return irr_data['grid'][tariff_idx][cap_idx]

# --- 3. SETTLEMENT LOGIC ---
def finalize_settlement():
    game["phase"] = "SETTLEMENT"
    remaining_cap = game["tender_capacity"]
    
    # Lowest tariff first; ties broken by earlier submission timestamp
    active = [t for t in game["teams"].values() if t["qualified"]]
    active.sort(key=lambda t: (t["current_tariff"], t["timestamp"]))
    
    for team in active:
        awarded = min(team["bid_mw"], max(0.0, remaining_cap))
        remaining_cap -= awarded
        team["awarded_mw"] = awarded
        
        # Land assignment (100 MW initial + random bonus)
        bonus_land = np.random.randint(10, 101)
        team["bonus_land"] = bonus_land
        total_land = 100.0 + bonus_land
        
        if awarded > 0:
            base = lookup_base_irr(team["current_tariff"], awarded)
            land_deficit = max(0.0, awarded - total_land)
            unawarded = team["bid_mw"] - awarded
            
            # Penalties: 1 bp = 0.0001
            land_pen = land_deficit * 0.0001
            unawarded_pen = unawarded * 0.0001
            
            final_irr = base - land_pen - unawarded_pen
            team["base_irr"] = base
            team["final_irr"] = final_irr
            team["meets_hurdle"] = (final_irr >= 0.14)
        else:
            team["base_irr"] = 0.0
            team["final_irr"] = 0.0
            team["meets_hurdle"] = False

# Auto-check 60s inactivity rule during live auction
if game["phase"] == "LIVE_RA" and game["last_bid_timestamp"] > 0:
    elapsed = time.time() - game["last_bid_timestamp"]
    if elapsed >= 60.0:
        finalize_settlement()

# --- 4. MODERATOR CONTROLS (SIDEBAR) ---
st.sidebar.title("Facilitator Panel")
st.sidebar.write(f"**Current Phase:** `{game['phase']}`")

if st.sidebar.button("Advance Phase / Execute Transition"):
    phase = game["phase"]
    if phase == "LOBBY":
        game["phase"] = "PHASE_1"
    elif phase == "PHASE_1":
        total_mw = sum(t["bid_mw"] for t in game["teams"].values())
        # Tender capacity: min(1500 MW, 80% total submitted)
        game["tender_capacity"] = min(1500.0, 0.80 * total_mw)
        game["phase"] = "PHASE_2"
    elif phase == "PHASE_2":
        # H1 Elimination: highest ceiling tariff dropped
        sorted_teams = sorted(
            game["teams"].values(),
            key=lambda t: (-t["ceiling_tariff"], t["timestamp"])
        )
        if sorted_teams:
            eliminated = sorted_teams[0]["name"]
            game["teams"][eliminated]["qualified"] = False
        game["phase"] = "LIVE_RA"
        game["last_bid_timestamp"] = time.time()
    elif phase == "LIVE_RA":
        finalize_settlement()
    st.rerun()

if st.sidebar.button("Reset Entire Game"):
    game["phase"] = "LOBBY"
    game["tender_capacity"] = 0.0
    game["last_bid_timestamp"] = 0.0
    game["teams"] = {}
    st.rerun()

# --- 5. MAIN INTERFACE ---
st.title("India RE Reverse Auction Simulator")

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("Your Team Portal")
    
    # Team Registration
    team_name = st.session_state.get("team_name")
    if not team_name:
        with st.form("login_form"):
            name_input = st.text_input("Enter Company Name:").strip()
            if st.form_submit_button("Join Game"):
                if name_input:
                    st.session_state["team_name"] = name_input
                    if name_input not in game["teams"]:
                        game["teams"][name_input] = {
                            "name": name_input,
                            "bid_mw": 0,
                            "ceiling_tariff": 0.0,
                            "current_tariff": 0.0,
                            "timestamp": time.time(),
                            "qualified": True,
                            "awarded_mw": 0,
                            "bonus_land": 0,
                            "base_irr": 0.0,
                            "final_irr": 0.0,
                            "meets_hurdle": False
                        }
                    st.rerun()
    else:
        st.success(f"Logged in as: **{team_name}**")
        my_data = game["teams"].get(team_name, {})

        if not my_data.get("qualified", True):
            st.error("Your company was eliminated under the H1 Ceiling Rule.")
        else:
            # Envelope I Submission
            if game["phase"] == "PHASE_1":
                with st.form("env1_form"):
                    st.write("**Envelope I: Technical Bid**")
                    mw = st.number_input("Bidding Capacity (MW)", min_value=50, max_value=750, step=10, value=100)
                    if st.form_submit_button("Submit Capacity"):
                        my_data["bid_mw"] = int(mw)
                        st.success(f"Submitted: {mw} MW")
                        st.rerun()

            # Envelope II Submission
            elif game["phase"] == "PHASE_2":
                with st.form("env2_form"):
                    st.write("**Envelope II: Financial Bid**")
                    t_val = st.number_input("Ceiling Tariff (INR/kWh)", min_value=1.00, max_value=5.00, step=0.01, value=2.70, format="%.2f")
                    if st.form_submit_button("Submit Ceiling Tariff"):
                        my_data["ceiling_tariff"] = float(t_val)
                        my_data["current_tariff"] = float(t_val)
                        my_data["timestamp"] = time.time()
                        st.success(f"Submitted Ceiling Tariff: {t_val:.2f}")
                        st.rerun()

            # Phase 3: Live Reverse Auction
            elif game["phase"] == "LIVE_RA":
                st.write(f"Your Current Active Tariff: **{my_data.get('current_tariff', 0.0):.2f} INR/kWh**")
                with st.form("bid_form"):
                    new_bid = st.number_input("Submit Undercut Tariff (INR/kWh)", min_value=1.00, max_value=5.00, step=0.01, value=my_data.get('current_tariff', 2.70) - 0.01, format="%.2f")
                    if st.form_submit_button("Submit Bid"):
                        if new_bid < my_data["current_tariff"]:
                            my_data["current_tariff"] = float(new_bid)
                            my_data["timestamp"] = time.time()
                            game["last_bid_timestamp"] = time.time()  # Reset 60-second inactivity ticker
                            st.success(f"Undercut bid of {new_bid:.2f} accepted.")
                            st.rerun()
                        else:
                            st.error("Bid must be strictly lower than your active tariff.")

with col_right:
    st.subheader("Live Tender Status & Merit Board")
    if game["tender_capacity"] > 0:
        st.info(f"**Effective Tender Volume:** {game['tender_capacity']:.1f} MW")
    
    if game["phase"] == "LIVE_RA" and game["last_bid_timestamp"] > 0:
        time_left = max(0, int(60 - (time.time() - game["last_bid_timestamp"])))
        st.metric(label="Inactivity Countdown (Ends at 0s)", value=f"{time_left}s")

    # Render Standings Table
    records = []
    teams_list = list(game["teams"].values())

    if game["phase"] == "LIVE_RA":
        teams_list.sort(key=lambda x: (x["current_tariff"], x["timestamp"]))
    elif game["phase"] == "SETTLEMENT":
        teams_list.sort(key=lambda x: -x["final_irr"])

    for idx, t in enumerate(teams_list):
        status = "Active" if t["qualified"] else "Eliminated (H1)"
        if game["phase"] == "SETTLEMENT":
            status = f"Awarded {t['awarded_mw']} MW | Net IRR: {t['final_irr']*100:.2f}% {'🏆' if t['meets_hurdle'] else '❌'}"
        
        prefix = f"L{idx+1} - " if game["phase"] == "LIVE_RA" and t["qualified"] else ""
        records.append({
            "Company": f"{prefix}{t['name']}",
            "Bid (MW)": t["bid_mw"],
            "Tariff (INR/kWh)": f"{t['current_tariff']:.2f}" if t["current_tariff"] > 0 else "--",
            "Status": status
        })

    if records:
        st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)

# Auto-refresh loop during the live auction so screens stay synced
if game["phase"] == "LIVE_RA":
    time.sleep(2)
    st.rerun()
