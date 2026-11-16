import time
import pandas as pd
import numpy as np
import streamlit as st
from datetime import datetime, timedelta
from kiteconnect import KiteConnect

st.set_page_config(page_title="NSE F&O Bullish Confluence Scanner", layout="wide")

# ==========================================
# 1. CREDENTIALS FROM STREAMLIT SECRETS
# ==========================================
API_KEY = st.secrets["KITE_API_KEY"]
API_SECRET = st.secrets["KITE_API_SECRET"]

# ==========================================
# 2. SIDEBAR: DAILY ZERODHA AUTH & PARAMETERS
# ==========================================
st.sidebar.header("1. Zerodha Session")
kite = KiteConnect(api_key=API_KEY)

if "access_token" not in st.session_state:
    st.session_state.access_token = ""

# Check if Zerodha redirected back with ?request_token=... in the URL
query_params = st.query_params
if "request_token" in query_params and not st.session_state.access_token:
    try:
        data = kite.generate_session(query_params["request_token"], api_secret=API_SECRET)
        st.session_state.access_token = data["access_token"]
        st.query_params.clear()
        st.sidebar.success("Connected via redirect!")
    except Exception as e:
        st.sidebar.error(f"Auto-login failed: {e}")

if not st.session_state.access_token:
    login_url = kite.login_url()
    st.sidebar.markdown(f"[Click here to Login to Zerodha]({login_url})")
    req_token_input = st.sidebar.text_input("Paste request_token from redirect URL:")
    if st.sidebar.button("Generate Session") and req_token_input:
        try:
            session_data = kite.generate_session(req_token_input.strip(), api_secret=API_SECRET)
            st.session_state.access_token = session_data["access_token"]
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Token error: {e}")
else:
    kite.set_access_token(st.session_state.access_token)
    st.sidebar.success("Zerodha Session Active")
    if st.sidebar.button("Reset Session"):
        st.session_state.access_token = ""
        st.rerun()

st.sidebar.header("2. Scanner Settings")
min_price = st.sidebar.number_input("Minimum Stock Price (₹)", value=200.0, step=50.0)
swing_len_4h = st.sidebar.slider("4H Swing Lookback (bars)", 5, 25, 10)
max_ext_pct = st.sidebar.slider("Max 4H Leg Extension (%)", 30, 80, 60) / 100.0
zone_prox_pct = st.sidebar.slider("Max Distance Above 4H Low (%)", 0.5, 4.0, 1.5)
explosive_mult = st.sidebar.slider("15m Explosive Body (x ATR)", 1.2, 3.5, 2.0)
min_rr_atr = st.sidebar.slider("Min Room to Resistance (x 15m ATR)", 1.0, 4.0, 2.5)

# ==========================================
# 3. CACHED INSTRUMENT & LTP FILTER
# ==========================================
@st.cache_data(ttl=3600)
def load_fno_universe(_kite):
    nfo_df = pd.DataFrame(_kite.instruments("NFO"))
    nse_df = pd.DataFrame(_kite.instruments("NSE"))

    # Active stock futures (near-month)
    fut_df = nfo_df[(nfo_df["segment"] == "NFO-FUT") & (nfo_df["instrument_type"] == "FUT")].copy()
    fut_df = fut_df.sort_values("expiry").groupby("name", as_index=False).first()

    # Match with NSE equity tokens
    eq_df = nse_df[nse_df["segment"] == "NSE"][["tradingsymbol", "instrument_token"]]
    merged = pd.merge(fut_df, eq_df, left_on="name", right_on="tradingsymbol", suffixes=("_fut", "_eq"))
    return merged[["name", "instrument_token_eq", "instrument_token_fut", "tradingsymbol_fut"]]

def filter_stocks_above_price(_kite, universe_df, min_p):
    symbols = [f"NSE:{sym}" for sym in universe_df["name"].tolist()]
    valid_symbols = {}
    for i in range(0, len(symbols), 500):
        ltp_resp = _kite.ltp(symbols[i:i + 500])
        for k, v in ltp_resp.items():
            if v["last_price"] > min_p:
                valid_symbols[k.split(":")[1]] = v["last_price"]

    filtered = universe_df[universe_df["name"].isin(valid_symbols.keys())].copy()
    filtered["ltp"] = filtered["name"].map(valid_symbols)
    return filtered

# ==========================================
# 4. MULTI-CONFLUENCE SCAN ENGINE
# ==========================================
def run_confluence_scan(_kite, candidates_df):
    results = []
    to_dt = datetime.now()
    from_4h = to_dt - timedelta(days=60)
    from_15m = to_dt - timedelta(days=10)

    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(candidates_df)

    for idx, row in candidates_df.reset_index(drop=True).iterrows():
        sym = row["name"]
        eq_token = int(row["instrument_token_eq"])
        fut_token = int(row["instrument_token_fut"])
        ltp = row["ltp"]

        status_text.text(f"Scanning ({idx + 1}/{total}): {sym} (₹{ltp:.2f})")
        progress_bar.progress((idx + 1) / total)

        try:
            time.sleep(0.35)
            data_4h = _kite.historical_data(eq_token, from_4h, to_dt, "240minute")
            df_4h = pd.DataFrame(data_4h)
            if len(df_4h) < swing_len_4h + 2:
                continue

            # 4H Swing Low & <60% Extension Check
            s_low = df_4h["low"].iloc[-(swing_len_4h + 1):-1].min()
            s_high = df_4h["high"].iloc[-(swing_len_4h + 1):-1].max()
            leg_range = s_high - s_low
            if leg_range <= 0:
                continue

            ext_ratio = (ltp - s_low) / leg_range
            near_4h_demand = (ltp >= s_low) and (ltp <= s_low * (1 + zone_prox_pct / 100.0))
            not_overextended = ext_ratio < max_ext_pct

            if not (near_4h_demand and not_overextended):
                continue

            # 15m Data for Explosive Demand Zone & Price Action
            time.sleep(0.35)
            data_15m = _kite.historical_data(eq_token, from_15m, to_dt, "15minute")
            df_15m = pd.DataFrame(data_15m)
            if len(df_15m) < 25:
                continue

            prev_close = df_15m["close"].shift(1)
            tr = pd.concat([
                df_15m["high"] - df_15m["low"],
                (df_15m["high"] - prev_close).abs(),
                (df_15m["low"] - prev_close).abs()
            ], axis=1).max(axis=1)
            df_15m["atr"] = tr.rolling(14).mean()
            df_15m["vol_sma"] = df_15m["volume"].rolling(20).mean()
            df_15m["ema20"] = df_15m["close"].ewm(span=20, adjust=False).mean()

            dz_top, dz_bot = None, None
            for i in range(len(df_15m) - 30, len(df_15m) - 1):
                if i < 20:
                    continue
                base = df_15m.iloc[i - 1]
                exp = df_15m.iloc[i]
                base_body = abs(base["close"] - base["open"])
                exp_body = exp["close"] - exp["open"]

                if (
                    base_body <= base["atr"] * 0.8
                    and exp_body >= exp["atr"] * explosive_mult
                    and exp["volume"] > exp["vol_sma"] * 1.3
                    and exp["close"] > base["high"]
                ):
                    candidate_top = max(base["open"], base["close"])
                    candidate_bot = min(base["low"], exp["low"])
                    if df_15m.iloc[i + 1:]["close"].min() >= candidate_bot:
                        dz_top, dz_bot = candidate_top, candidate_bot

            if dz_top is None:
                continue

            last = df_15m.iloc[-1]
            prev = df_15m.iloc[-2]
            near_15m_dz = (last["low"] <= dz_top * 1.003) and (last["close"] >= dz_bot)

            candle_range = max(last["high"] - last["low"], 0.01)
            bull_pa = (
                last["close"] > last["open"]
                and (last["close"] - last["low"]) >= 0.6 * candle_range
                and last["close"] > prev["close"]
                and last["close"] > last["ema20"]
            )

            step = 100.0 if ltp >= 1000 else 50.0
            psych_res = np.ceil(ltp / step) * step
            nearest_res = min(s_high, psych_res)
            clear_res = (nearest_res - ltp) >= (last["atr"] * min_rr_atr)

            if not (near_15m_dz and bull_pa and clear_res):
                continue

            time.sleep(0.35)
            fut_15m = _kite.historical_data(fut_token, to_dt - timedelta(days=3), to_dt, "15minute")
            df_fut = pd.DataFrame(fut_15m)
            if len(df_fut) < 2:
                continue

            fut_rising = (
                df_fut.iloc[-1]["close"] > df_fut.iloc[-2]["close"]
                and df_fut.iloc[-1]["close"] >= ltp * 0.998
            )

            if fut_rising:
                results.append({
                    "Symbol": sym,
                    "Spot LTP (₹)": round(ltp, 2),
                    "4H Swing Low": round(s_low, 2),
                    "4H Leg Ext %": round(ext_ratio * 100, 1),
                    "15m Demand Zone": f"{round(dz_bot, 2)} - {round(dz_top, 2)}",
                    "Nearest Resistance": round(nearest_res, 2),
                    "Futures LTP": round(df_fut.iloc[-1]["close"], 2),
                })

        except Exception:
            continue

    status_text.empty()
    progress_bar.empty()
    return pd.DataFrame(results)

# ==========================================
# 5. MAIN DASHBOARD UI
# ==========================================
st.title("NSE F&O High-Probability Bullish Confluence Scanner")
st.caption("Filters: F&O > ₹200 | Near 4H Swing Low (<60% leg) | 15m Explosive Demand | Rising Price & Futures | Clear Overhead Resistance")

if not st.session_state.access_token:
    st.info("Log in with your Zerodha Kite Connect account in the left sidebar to run the live scanner.")
else:
    if st.button("Run Bullish Confluence Scan", type="primary"):
        with st.spinner("Loading F&O universe and filtering stocks > ₹200..."):
            universe = load_fno_universe(kite)
            candidates = filter_stocks_above_price(kite, universe, min_price)
            st.write(f"**{len(candidates)}** F&O stocks trading above **₹{min_price:.0f}** queued for multi-timeframe analysis.")

        df_matches = run_confluence_scan(kite, candidates)

        if df_matches.empty:
            st.warning("No F&O stocks currently meet all 4H + 15m + Futures + Resistance confluences.")
        else:
            st.success(f"Found {len(df_matches)} high-probability bullish setup(s)!")
            st.dataframe(df_matches, use_container_width=True)
