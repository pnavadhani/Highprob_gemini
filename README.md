# NSE F&O Bullish Confluence Scanner

A cloud-based Python scanner built with Streamlit and Zerodha Kite Connect that screens NSE F&O stocks (priced > ₹200) for multi-timeframe bullish confluences:
1. 4H Macro Demand support (<60% retracement of swing high-low leg).
2. 15m Explosive Demand Zone retests.
3. 15m Price Action & EMA trend momentum.
4. Near-Month Futures (`1!`) positive basis / upward momentum.
5. Overhead Resistance / Psychological barrier clearance.

## Setup Instructions
1. Deploy this repository on Streamlit Community Cloud.
2. Under app **Settings -> Secrets**, configure your Zerodha API keys:
   ```toml
   KITE_API_KEY = "your_api_key"
   KITE_API_SECRET = "your_api_secret"
