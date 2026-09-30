import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np
import json
import time
import os

st.set_page_config(page_title="Wholesale Power Market Sim", layout="wide")

# Sync state across browsers every 2 seconds
st_autorefresh(interval=2000, key="market_sync")

STATE_FILE = "market_state.json"
ADMIN_KEY = "admin2026"
PRICE_CAP = 300.0  # Maximum market offer ceiling ($/MWh)

# Identical Fleet Specification per Team (600 MW Total)
FLEET = {
    "Green": {"mw": 200, "mc": 0.0, "color": "#2ca02c"},
    "MidMerit": {"mw": 250, "mc": 35.0, "color": "#1f77b4"},
    "Peaker": {"mw": 150, "mc": 75.0, "color": "#d62728"}
}

# ==========================================
# 1. CENTRAL DISK-BASED STATE ENGINE
# ==========================================
def init_default_state():
    return {
        "game_id": 1,
        "round_number": 1,
        "phase": "LOBBY",  # LOBBY, BIDDING, SETTLEMENT
        "system_demand": 1200.0,
        "clearing_price": 0.0,
        "cleared_mw": 0.0,
        "marginal_unit": "None",
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
# 4. MARKET CLEARING ENGINE (UNIFORM PRICE)
# ==========================================
def clear_market(state):
    demand_remaining = state["system_demand"]
    bids = []

    for t_name, t_data in state["teams"].items():
        for unit_key, spec in FLEET.items():
            bid_p = t_data["current_bids"].get(unit_key, spec["mc"])
            bids.append({
                "team": t_name,
                "unit": unit_key,
                "mw": spec["mw"],
                "mc": spec["mc"],
                "price": bid_p,
                "timestamp": t_data.get("timestamp", 0)
            })

    bids.sort(key=lambda x: (x["price"], x["timestamp"]))

    mcp = 0.0
    cleared_volume = 0.0
    marginal_label = "Unserved Demand / Deficit"

    for t in state["teams"].values():
        t["round_dispatched_mw"] = 0.0
        t["round_profit"] = 0.0
        t["tranche_results"] = {}

    for b in bids:
        t_obj = state["teams"][b["team"]]
        if demand_remaining > 0:
            awarded = min(b["mw"], demand_remaining)
            demand_remaining -= awarded
            cleared_volume += awarded
            mcp = b["price"]
            marginal_label = f"{b['team']} ({b['unit']}) @ ${mcp:.2f}"

            t_obj["round_dispatched_mw"] += awarded
            t_obj["tranche_results"][b["unit"]] = {
                "bid": b["price"],
                "mc": b["mc"],
                "awarded_mw": awarded
            }
        else:
            t_obj["tranche_results"][b["unit"]] = {
                "bid": b["price"],
                "mc": b["mc"],
                "awarded_mw": 0.0
            }

    if demand_remaining > 0:
        mcp = PRICE_CAP
        marginal_label = f"Deficit ({demand_remaining:.0f} MW Unserved) -> Price Cap"

    state["clearing_price"] = mcp
    state["cleared_mw"] = cleared_volume
    state["marginal_unit"] = marginal_label

    for t in state["teams"].values():
        total_p = 0.0
        for unit_key, res in t.get("tranche_results", {}).items():
            margin = mcp - res["mc"]
            total_p += margin * res["awarded_mw"]
        t["round_profit"] = total_p
        t["cumulative_profit"] = t.get("cumulative_profit", 0.0) + total_p

    state["phase"] = "SETTLEMENT"
    save_game(state)

# ==========================================
# 5. FACILITATOR SIDEBAR & AUTO-SIZING
# ==========================================
st.sidebar.title("Facilitator Panel")

if not st.session_state["is_admin"]:
    with st.sidebar.expander("Facilitator Login"):
        entered = st.text_input("Admin Key", type="password")
        if st.button("Authenticate"):
            if entered == ADMIN_KEY:
                st.session_state["is_admin"] = True
                st.sidebar.success("Logged in as Facilitator")
                st.rerun()
            else:
                st.sidebar.error("Invalid passcode")
else:
    st.sidebar.success("Logged in as Facilitator")
    if st.sidebar.button("Logout Facilitator"):
        st.session_state["is_admin"] = False
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.subheader("Market Controls")
    st.sidebar.write(f"**Phase:** `{game['phase']}` | **Round:** `{game['round_number']}`")

    n_teams = max(1, len(game["teams"]))
    total_capacity = n_teams * 600

    # Auto-calculated scenario presets
    preset_oversupply = float(n_teams * 250)
    preset_tight = float(n_teams * 500)
    preset_scarcity = float(n_teams * 550)

    st.sidebar.markdown(f"**Joined Teams:** `{n_teams}` | **Fleet Capacity:** `{total_capacity} MW`")

    # Fast-set buttons based on team count
    st.sidebar.write("**Dynamic Demand Presets:**")
    cp1, cp2, cp3 = st.sidebar.columns(3)

    if cp1.button("Oversupply", help=f"Set to {preset_oversupply:.0f} MW (~40% capacity)"):
        s = load_game()
        s["system_demand"] = preset_oversupply
        save_game(s)
        st.rerun()

    if cp2.button("Tight", help=f"Set to {preset_tight:.0f} MW (~80% capacity)"):
        s = load_game()
        s["system_demand"] = preset_tight
        save_game(s)
        st.rerun()

    if cp3.button("Scarcity", help=f"Set to {preset_scarcity:.0f} MW (~90% capacity)"):
        s = load_game()
        s["system_demand"] = preset_scarcity
        save_game(s)
        st.rerun()

    # Manual slider adjustment
    max_market_mw = max(600, total_capacity)
    curr_demand_val = min(float(game["system_demand"]), float(max_market_mw))
    
    new_demand = st.sidebar.slider(
        "System Target Demand (MW)",
        min_value=100,
        max_value=max_market_mw,
        step=50,
        value=int(curr_demand_val)
    )

    if new_demand != game["system_demand"] and game["phase"] != "SETTLEMENT":
        s = load_game()
        s["system_demand"] = float(new_demand)
        save_game(s)
        st.rerun()

    st.sidebar.markdown("---")

    # Phase Advancement
    if game["phase"] == "LOBBY":
        if st.sidebar.button("Open Bidding Round", type="primary"):
            s = load_game()
            s["phase"] = "BIDDING"
            s["system_demand"] = float(new_demand)
            for t in s["teams"].values():
                t["submitted"] = False
            save_game(s)
            st.rerun()

    elif game["phase"] == "BIDDING":
        submitted_count = sum(1 for t in game["teams"].values() if t.get("submitted", False))
        st.sidebar.info(f"Bids Received: {submitted_count}/{len(game['teams'])}")
        if st.sidebar.button("Execute Dispatch & Clear Market", type="primary"):
            s = load_game()
            clear_market(s)
            st.rerun()

    elif game["phase"] == "SETTLEMENT":
        if st.sidebar.button("Next Round (Reset Bids)", type="primary"):
            s = load_game()
            s["round_number"] += 1
            s["phase"] = "BIDDING"
            for t in s["teams"].values():
                t["submitted"] = False
            save_game(s)
            st.rerun()

    st.sidebar.markdown("---")
    if st.sidebar.button("Reset Entire Game"):
        fresh = init_default_state()
        fresh["game_id"] = game["game_id"] + 1
        save_game(fresh)
        st.rerun()

# ==========================================
# 6. MAIN USER INTERFACE
# ==========================================
st.title("⚡ Wholesale Power Market Simulator")
st.caption("Uniform Clearing Price (Pay-as-Cleared) • Hourly Day-Ahead Dispatch")

col_left, col_right = st.columns([1, 1])

# --- LEFT COLUMN: TEAM PORTAL ---
with col_left:
    st.subheader("Generation Portfolio")

    if st.session_state["is_admin"]:
        st.info("Facilitator View: Adjust system demand and execute dispatch rounds via the sidebar.")
    else:
        current_team = st.session_state.get("team_name")
        if not current_team:
            with st.form("join_form"):
                team_input = st.text_input("Utility / Generator Name:").strip()
                if st.form_submit_button("Join Market"):
                    if team_input:
                        st.session_state["team_name"] = team_input
                        st.query_params["team"] = team_input
                        s = load_game()
                        if team_input not in s["teams"]:
                            s["teams"][team_input] = {
                                "name": team_input,
                                "current_bids": {k: v["mc"] for k, v in FLEET.items()},
                                "submitted": False,
                                "timestamp": time.time(),
                                "round_dispatched_mw": 0.0,
                                "round_profit": 0.0,
                                "cumulative_profit": 0.0
                            }
                            save_game(s)
                        st.rerun()
        else:
            st.success(f"Logged in as: **{current_team}**")

            s = load_game()
            if current_team not in s["teams"]:
                s["teams"][current_team] = {
                    "name": current_team,
                    "current_bids": {k: v["mc"] for k, v in FLEET.items()},
                    "submitted": False,
                    "timestamp": time.time(),
                    "round_dispatched_mw": 0.0,
                    "round_profit": 0.0,
                    "cumulative_profit": 0.0
                }
                save_game(s)

            my_data = s["teams"][current_team]

            if game["phase"] == "LOBBY":
                st.info("Market is in Lobby. Waiting for the facilitator to open Round 1...")

            elif game["phase"] == "BIDDING":
                st.markdown(f"**Target System Demand:** `{game['system_demand']:.0f} MW`")
                st.write("Submit your offer price ($/MWh) for each generation tranche:")

                with st.form("offer_form"):
                    bids_to_submit = {}
                    for unit_key, spec in FLEET.items():
                        c1, c2 = st.columns([2, 1])
                        c1.write(f"**{unit_key}** ({spec['mw']} MW)")
                        c1.caption(f"Marginal Fuel Cost: ${spec['mc']:.2f}/MWh")
                        bids_to_submit[unit_key] = c2.number_input(
                            f"Offer ($/MWh)",
                            min_value=float(spec["mc"]),
                            max_value=float(PRICE_CAP),
                            step=1.0,
                            value=float(my_data["current_bids"].get(unit_key, spec["mc"])),
                            key=f"bid_{unit_key}"
                        )

                    if st.form_submit_button("Submit Offer Portfolio"):
                        s = load_game()
                        s["teams"][current_team]["current_bids"] = bids_to_submit
                        s["teams"][current_team]["submitted"] = True
                        s["teams"][current_team]["timestamp"] = time.time()
                        save_game(s)
                        st.success("Offers locked in. Waiting for market clearing...")
                        st.rerun()

                if my_data.get("submitted", False):
                    st.success("✅ Offers submitted for this round.")

            elif game["phase"] == "SETTLEMENT":
                st.markdown(f"### Round {game['round_number']} Settlement")
                mcp = game["clearing_price"]
                r_profit = my_data.get("round_profit", 0.0)
                tot_profit = my_data.get("cumulative_profit", 0.0)

                c1, c2 = st.columns(2)
                c1.metric("Market Clearing Price", f"${mcp:.2f} / MWh")
                c2.metric("Round Operating Profit", f"${r_profit:,.2f}")

                st.markdown("---")
                st.write("**Fleet Performance:**")
                records = []
                for unit_key, res in my_data.get("tranche_results", {}).items():
                    margin = max(0.0, mcp - res["mc"]) if res["awarded_mw"] > 0 else 0.0
                    records.append({
                        "Asset": unit_key,
                        "Capacity": f"{FLEET[unit_key]['mw']} MW",
                        "Fuel Cost": f"${res['mc']:.2f}",
                        "Your Bid": f"${res['bid']:.2f}",
                        "Dispatched": f"{res['awarded_mw']:.0f} MW",
                        "Margin Earned": f"${margin:.2f}/MWh",
                        "Tranche Profit": f"${margin * res['awarded_mw']:,.2f}"
                    })
                st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
                st.metric("Total Cumulative Bank Balance", f"${tot_profit:,.2f}")

# --- RIGHT COLUMN: MARKET STACK & LEADERBOARD ---
with col_right:
    st.subheader("Market Clearing Board")

    if game["phase"] in ["LOBBY", "BIDDING"]:
        st.info(f"Target System Demand: **{game['system_demand']:.0f} MW**")
        st.markdown("### Submission Status")
        status_table = []
        for t in game["teams"].values():
            status_table.append({
                "Participant": t["name"],
                "Status": "✅ Offers Ready" if t.get("submitted", False) else "⏳ Formulating Strategy"
            })
        if status_table:
            st.dataframe(pd.DataFrame(status_table), use_container_width=True, hide_index=True)

    elif game["phase"] == "SETTLEMENT":
        st.success(f"Market Cleared @ **${game['clearing_price']:.2f} / MWh** (Dispatched: {game['cleared_mw']:.0f} MW)")
        st.caption(f"Marginal Unit: **{game['marginal_unit']}**")

        st.markdown("### 🏆 Profit Leaderboard")
        leaderboard = []
        for t in game["teams"].values():
            leaderboard.append({
                "Generator": t["name"],
                "Round Dispatched (MW)": f"{t.get('round_dispatched_mw', 0):.0f} MW",
                "Round Profit": t.get("round_profit", 0.0),
                "Total Wealth": t.get("cumulative_profit", 0.0)
            })
        leaderboard.sort(key=lambda x: -x["Total Wealth"])
        df_lb = pd.DataFrame(leaderboard)
        df_lb["Round Profit"] = df_lb["Round Profit"].apply(lambda v: f"${v:,.2f}")
        df_lb["Total Wealth"] = df_lb["Total Wealth"].apply(lambda v: f"${v:,.2f}")
        st.dataframe(df_lb, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("### Complete Merit Order Stack")
        all_tranches = []
        for t_name, t in game["teams"].items():
            for u_k, u_res in t.get("tranche_results", {}).items():
                all_tranches.append({
                    "Team": t_name,
                    "Unit": u_k,
                    "Bid Price": u_res["bid"],
                    "Capacity": FLEET[u_k]["mw"],
                    "Cleared": u_res["awarded_mw"]
                })
        all_tranches.sort(key=lambda x: x["Bid Price"])
        df_stack = pd.DataFrame(all_tranches)
        df_stack["Bid Price"] = df_stack["Bid Price"].apply(p: f"${p:.2f}")
        df_stack["Cleared"] = df_stack["Cleared"].apply(m: f"{m:.0f} MW")
        st.dataframe(df_stack, use_container_width=True, hide_index=True)
