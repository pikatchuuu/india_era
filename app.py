import streamlit as st
import pandas as pd
import numpy as np
import json
import time

st.set_page_config(page_title="India RE Reverse Auction", layout="wide")

ADMIN_KEY = "admin2026"

if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False

if st.query_params.get("role") == "admin":
    st.session_state["is_admin"] = True

@st.cache_resource
def get_global_game_state():
    return {
        "game_id": 1,
        "phase": "LOBBY",
        "tender_capacity": 0.0,
        "last_bid_timestamp": 0.0,
        "teams": {}
    }

game = get_global_game_state()

if st.session_state.get("active_game_id") != game["game_id"]:
    st.session_state["team_name"] = None
    st.session_state["active_game_id"] = game["game_id"]

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
    return float(irr_data['grid'][tariff_idx][cap_idx])

def advance_game_phase():
    phase = game["phase"]
    if phase == "LOBBY":
        game["phase"] = "PHASE_1"
    elif phase == "PHASE_1":
        total_mw = sum(t["bid_mw"] for t in game["teams"].values())
        game["tender_capacity"] = min(1500.0, 0.80 * total_mw)
        game["phase"] = "PHASE_2"
    elif phase == "PHASE_2":
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

def finalize_settlement():
    game["phase"] = "SETTLEMENT"
    remaining_cap = game["tender_capacity"]
    active = [t for t in game["teams"].values() if t["qualified"]]
    active.sort(key=lambda t: (t["current_tariff"], t["timestamp"]))
    
    for team in active:
        awarded = min(team["bid_mw"], max(0.0, remaining_cap))
        remaining_cap -= awarded
        team["awarded_mw"] = awarded
        
        bonus_land = np.random.randint(10, 101)
        team["bonus_land"] = bonus_land
        total_land = 100.0 + bonus_land
        
        if awarded > 0:
            base = lookup_base_irr(team["current_tariff"], awarded)
            land_deficit = max(0.0, awarded - total_land)
            unawarded = team["bid_mw"] - awarded
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

if game["phase"] == "LIVE_RA" and game["last_bid_timestamp"] > 0:
    elapsed = time.time() - game["last_bid_timestamp"]
    if elapsed >= 60.0:
        finalize_settlement()

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
    
    if st.sidebar.button("Advance Phase / Execute Transition", type="primary"):
        advance_game_phase()
        st.rerun()

    if st.sidebar.button("Start New Game / Reset All"):
        game["game_id"] += 1
        game["phase"] = "LOBBY"
        game["tender_capacity"] = 0.0
        game["last_bid_timestamp"] = 0.0
        game["teams"].clear()
        st.sidebar.success("New game initialized. All sessions cleared.")
        st.rerun()

st.title("India RE Reverse Auction Simulator")
col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("Your Team Portal")
    if st.session_state["is_admin"]:
        st.info("You are logged in as the Facilitator. Use the sidebar controls to advance rounds or reset.")
    else:
        team_name = st.session_state.get("team_name")
        if not team_name:
            with st.form("login_form"):
                name_input = st.text_input("Company Name:").strip()
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
                if game["phase"] == "PHASE_1":
                    with st.form("env1_form"):
                        st.write("**Envelope I: Technical Bid**")
                        mw = st.number_input("Bidding Capacity (MW) [50-750 MW in increments of 10]", min_value=50, max_value=750, step=10, value=100)
                        if st.form_submit_button("Submit Capacity"):
                            my_data["bid_mw"] = int(mw)
                            st.success(f"Submitted: {mw} MW")
                            st.rerun()
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
                elif game["phase"] == "LIVE_RA":
                    st.write(f"Your Active Tariff: **{my_data.get('current_tariff', 0.0):.2f} INR/kWh**")
                    with st.form("bid_form"):
                        default_undercut = max(1.00, my_data.get('current_tariff', 2.70) - 0.01)
                        new_bid = st.number_input("Submit Undercut Tariff (INR/kWh)", min_value=1.00, max_value=5.00, step=0.01, value=default_undercut, format="%.2f")
                        if st.form_submit_button("Submit Bid"):
                            if new_bid < my_data["current_tariff"]:
                                my_data["current_tariff"] = float(new_bid)
                                my_data["timestamp"] = time.time()
                                game["last_bid_timestamp"] = time.time()
                                st.success(f"Undercut bid of {new_bid:.2f} accepted.")
                                st.rerun()
                            else:
                                st.error("Bid must be strictly lower than your active tariff.")
                elif game["phase"] == "LOBBY":
                    st.info("Waiting for the Facilitator to start Envelope I...")
                elif game["phase"] == "SETTLEMENT":
                    st.info("The auction has concluded. See the results board on the right.")

with col_right:
    st.subheader("Tender Status & Standings")
    if game["tender_capacity"] > 0:
        st.info(f"**Effective Tender Volume:** {game['tender_capacity']:.1f} MW")
    
    if game["phase"] == "LIVE_RA" and game["last_bid_timestamp"] > 0:
        time_left = max(0, int(60 - (time.time() - game["last_bid_timestamp"])))
        st.metric(label="Inactivity Countdown (Ends at 0s)", value=f"{time_left}s")

    records = []
    teams_list = list(game["teams"].values())
    if game["phase"] == "LIVE_RA":
        teams_list.sort(key=lambda x: (x["current_tariff"], x["timestamp"]))
    elif game["phase"] == "SETTLEMENT":
        teams_list.sort(key=lambda x: -x["final_irr"])

    for idx, t in enumerate(teams_list):
        status = "Active" if t["qualified"] else "Eliminated (H1)"
        if game["phase"] == "SETTLEMENT":
            status = f"Awarded {t['awarded_mw']} MW | Net IRR: {t['final_irr']*100:.2f}% {'🏆 Pass' if t['meets_hurdle'] else '❌ Fail (<14%)'}"
        
        prefix = f"L{idx+1} - " if game["phase"] == "LIVE_RA" and t["qualified"] else ""
        records.append({
            "Company": f"{prefix}{t['name']}",
            "Bid (MW)": t["bid_mw"],
            "Current Tariff": f"{t['current_tariff']:.2f}" if t["current_tariff"] > 0 else "--",
            "Status": status
        })

    if records:
        st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)

if game["phase"] == "LIVE_RA":
    time.sleep(2)
    st.rerun()
