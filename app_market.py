import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import json
import time
import os

st.set_page_config(page_title="Wholesale Power Market Sim", layout="wide")

st_autorefresh(interval=2000, key="market_sync")

STATE_FILE = "market_state.json"
ADMIN_KEY = "admin2026"
PRICE_CAP = 300.0

FLEET = {
    "Green": {"mw": 200, "mc": 0.0, "color": "#2ca02c"},
    "MidMerit": {"mw": 250, "mc": 35.0, "color": "#1f77b4"},
    "Peaker": {"mw": 150, "mc": 75.0, "color": "#d62728"}
}

def init_default_state():
    return {
        "game_id": 1,
        "round_number": 1,
        "phase": "LOBBY",
        "expected_demand": 1200.0,
        "demand_std_pct": 0.05,
        "actual_demand": 1200.0,
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

url_team = st.query_params.get("team")
if url_team:
    st.session_state["team_name"] = url_team
elif "team_name" in st.session_state and st.session_state["team_name"]:
    st.query_params["team"] = st.session_state["team_name"]

if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False

if st.query_params.get("role") == "admin":
    st.session_state["is_admin"] = True

def clear_market(state):
    mean_d = state.get("expected_demand", 1200.0)
    std_pct = state.get("demand_std_pct", 0.05)
    std_dev = mean_d * std_pct
    
    realized_demand = max(0.0, round(float(np.random.normal(mean_d, std_dev)), 1))
    state["actual_demand"] = realized_demand
    
    demand_remaining = realized_demand

    bids = []
    for t_name, t_data in state["teams"].items():
        for unit_key, spec in FLEET.items():
            bid_p = t_data["current_bids"].get(unit_key, spec["mc"])
            bids.append({
                "team": t_name,
                "unit": unit_key,
                "mw": spec["mw"],
                "mc": spec["mc"],
                "price": bid_p
            })

    for t in state["teams"].values():
        t["round_dispatched_mw"] = 0.0
        t["round_profit"] = 0.0
        t["tranche_results"] = {
            unit_key: {
                "bid": t["current_bids"].get(unit_key, FLEET[unit_key]["mc"]),
                "mc": FLEET[unit_key]["mc"],
                "awarded_mw": 0.0
            }
            for unit_key in FLEET
        }

    bids_by_price = {}
    for b in bids:
        bids_by_price.setdefault(b["price"], []).append(b)

    mcp = 0.0
    cleared_volume = 0.0
    marginal_label = "Unserved Demand / Deficit"

    for price in sorted(bids_by_price.keys()):
        tier_bids = bids_by_price[price]
        tier_total_mw = sum(b["mw"] for b in tier_bids)

        if demand_remaining <= 0:
            break

        mcp = price

        if tier_total_mw <= demand_remaining:
            for b in tier_bids:
                awarded = float(b["mw"])
                state["teams"][b["team"]]["tranche_results"][b["unit"]]["awarded_mw"] = awarded
                state["teams"][b["team"]]["round_dispatched_mw"] += awarded

            demand_remaining -= tier_total_mw
            cleared_volume += tier_total_mw
            marginal_label = f"Fully Cleared @ ${mcp:.2f}"
        else:
            allocation_ratio = demand_remaining / tier_total_mw

            for b in tier_bids:
                awarded = float(b["mw"]) * allocation_ratio
                state["teams"][b["team"]]["tranche_results"][b["unit"]]["awarded_mw"] = awarded
                state["teams"][b["team"]]["round_dispatched_mw"] += awarded

            cleared_volume += demand_remaining
            marginal_label = f"Marginal Tie ({len(tier_bids)} units split) @ ${mcp:.2f}"
            demand_remaining = 0.0

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

# Facilitator Panel
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

    preset_oversupply = float(n_teams * 250)
    preset_tight = float(n_teams * 500)
    preset_scarcity = float(n_teams * 550)

    st.sidebar.markdown(f"**Joined Teams:** `{n_teams}` | **Fleet Capacity:** `{total_capacity} MW`")

    st.sidebar.write("**Expected Demand Presets:**")
    cp1, cp2, cp3 = st.sidebar.columns(3)

    if cp1.button("Oversupply"):
        s = load_game()
        s["expected_demand"] = preset_oversupply
        save_game(s)
        st.rerun()

    if cp2.button("Tight"):
        s = load_game()
        s["expected_demand"] = preset_tight
        save_game(s)
        st.rerun()

    if cp3.button("Scarcity"):
        s = load_game()
        s["expected_demand"] = preset_scarcity
        save_game(s)
        st.rerun()

    max_market_mw = max(600, total_capacity)
    curr_exp_val = min(float(game.get("expected_demand", 1200.0)), float(max_market_mw))

    new_exp_demand = st.sidebar.slider(
        "Expected System Demand (MW)",
        min_value=100,
        max_value=max_market_mw,
        step=50,
        value=int(curr_exp_val)
    )

    demand_std_pct = st.sidebar.slider(
        "Demand Uncertainty (Std Dev %)",
        min_value=0.0,
        max_value=0.20,
        step=0.01,
        value=float(game.get("demand_std_pct", 0.05)),
        format="%.0f%%"
    )

    if (new_exp_demand != game.get("expected_demand") or demand_std_pct != game.get("demand_std_pct")) and game["phase"] != "SETTLEMENT":
        s = load_game()
        s["expected_demand"] = float(new_exp_demand)
        s["demand_std_pct"] = float(demand_std_pct)
        save_game(s)
        st.rerun()

    st.sidebar.markdown("---")

    if game["phase"] == "LOBBY":
        if st.sidebar.button("Open Bidding Round", type="primary"):
            s = load_game()
            s["phase"] = "BIDDING"
            s["expected_demand"] = float(new_exp_demand)
            s["demand_std_pct"] = float(demand_std_pct)
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

# Main Interface
st.title("⚡ Wholesale Power Market Simulator")
st.caption("Uniform Clearing Price • Proportional Marginal Tie-Breaking • Stochastic Demand")

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("Generation Portfolio")

    if st.session_state["is_admin"]:
        st.info("Facilitator View: Adjust expected demand and uncertainty via the sidebar.")
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
                st.info("Market is in Lobby. Waiting for facilitator to start...")

            elif game["phase"] == "BIDDING":
                exp_d = game.get("expected_demand", 1200.0)
                std_pct = game.get("demand_std_pct", 0.05)
                st.markdown(f"**Expected Demand:** `{exp_d:.0f} MW` (Uncertainty: `±{exp_d * std_pct:.0f} MW` / `1σ`)")
                st.write("Submit your offer price ($/MWh) for each generation tranche:")

                with st.form("offer_form"):
                    bids_to_submit = {}
                    for unit_key, spec in FLEET.items():
                        c1, c2 = st.columns([2, 1])
                        c1.write(f"**{unit_key}** ({spec['mw']} MW)")
                        c1.caption(f"Marginal Cost: ${spec['mc']:.2f}/MWh")
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
                        st.success("Offers locked in.")
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
                        "Dispatched": f"{res['awarded_mw']:.1f} MW",
                        "Margin Earned": f"${margin:.2f}/MWh",
                        "Tranche Profit": f"${margin * res['awarded_mw']:,.2f}"
                    })
                st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
                st.metric("Total Cumulative Bank Balance", f"${tot_profit:,.2f}")

with col_right:
    st.subheader("Market Clearing Board")

    if game["phase"] in ["LOBBY", "BIDDING"]:
        exp_d = game.get("expected_demand", 1200.0)
        std_pct = game.get("demand_std_pct", 0.05)
        st.info(f"Expected Demand: **{exp_d:.0f} MW** (Std Dev: **±{exp_d * std_pct:.0f} MW**)")
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
        act_d = game.get("actual_demand", game["cleared_mw"])
        exp_d = game.get("expected_demand", act_d)
        st.success(f"Market Cleared @ **${game['clearing_price']:.2f} / MWh**")
        st.caption(f"Realized Demand: **{act_d:.1f} MW** (Expected: {exp_d:.0f} MW) | Marginal State: **{game['marginal_unit']}**")

        # Supply Curve Chart
        st.markdown("### 📊 Merit Order Supply Curve")
        all_tranches = []
        for t_name, t in game["teams"].items():
            for u_k, u_res in t.get("tranche_results", {}).items():
                all_tranches.append({
                    "Team": t_name,
                    "Unit": u_k,
                    "Bid Price": u_res["bid"],
                    "Capacity": FLEET[u_k]["mw"]
                })

        all_tranches.sort(key=lambda x: x["Bid Price"])

        x_coords = [0.0]
        y_coords = []
        hover_text = []

        cum_mw = 0.0
        for item in all_tranches:
            p = item["Bid Price"]
            cap = item["Capacity"]
            label = f"Team: {item['Team']}<br>Unit: {item['Unit']}<br>Offer: ${p:.2f}/MWh<br>Capacity: {cap} MW"

            y_coords.append(p)
            hover_text.append(label)

            cum_mw += cap
            x_coords.append(cum_mw)
            y_coords.append(p)
            hover_text.append(label)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=x_coords,
            y=y_coords,
            mode='lines',
            name='Supply Curve',
            line=dict(color='#1f77b4', width=3, shape='hv'),
            text=hover_text,
            hoverinfo='text+x+y'
        ))

        fig.add_vline(
            x=act_d,
            line_dash="dash",
            line_color="red",
            annotation_text=f"Cleared Demand: {act_d:.1f} MW",
            annotation_position="top left"
        )

        fig.add_hline(
            y=game["clearing_price"],
            line_dash="dot",
            line_color="green",
            annotation_text=f"MCP: ${game['clearing_price']:.2f}",
            annotation_position="bottom right"
        )

        fig.update_layout(
            xaxis_title="Cumulative Capacity (MW)",
            yaxis_title="Offer Price ($/MWh)",
            margin=dict(l=20, r=20, t=30, b=20),
            height=380,
            hovermode="x unified"
        )

        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")
        st.markdown("### 🏆 Profit Leaderboard")
        leaderboard = []
        for t in game["teams"].values():
            leaderboard.append({
                "Generator": t["name"],
                "Round Dispatched": f"{t.get('round_dispatched_mw', 0):.1f} MW",
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
        df_stack = pd.DataFrame(all_tranches)
        df_stack["Cleared"] = [game["teams"][x["Team"]]["tranche_results"][x["Unit"]]["awarded_mw"] for x in all_tranches]
        df_stack["Bid Price"] = df_stack["Bid Price"].apply(lambda p: f"${p:.2f}")
        df_stack["Cleared"] = df_stack["Cleared"].apply(lambda m: f"{m:.1f} MW")
        st.dataframe(df_stack, use_container_width=True, hide_index=True)
