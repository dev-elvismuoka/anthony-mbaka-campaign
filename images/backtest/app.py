import streamlit as st
import pandas as pd
import yfinance as yf
import ccxt
import time
from datetime import datetime, timedelta, time as dt_time

# ==========================================
# 1. DATA DOWNLOADER ENGINE
# ==========================================

def download_crypto_history(symbol, timeframe, days):
    """
    Forces 3 years of data from Binance using CCXT (Free & Legal)
    """
    exchange = ccxt.binance()
    # Map timeframe: '5m' -> '5m', '1m' -> '1m'
    
    # Calculate start time in milliseconds
    since = exchange.milliseconds() - (days * 24 * 60 * 60 * 1000)
    
    all_candles = []
    
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    while since < exchange.milliseconds():
        current_date = datetime.fromtimestamp(since / 1000).strftime('%Y-%m-%d')
        status_text.text(f"Downloading {symbol} data from {current_date}...")
        
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since, limit=1000)
            if len(ohlcv) == 0:
                break
            
            since = ohlcv[-1][0] + 1 # Move to next timestamp
            all_candles += ohlcv
            
            # Update progress (Rough estimate)
            elapsed = (since - (exchange.milliseconds() - (days * 86400000))) 
            total_duration = days * 86400000
            progress = min(elapsed / total_duration, 1.0)
            progress_bar.progress(progress)
            
            time.sleep(0.1) # Prevent ban
            
        except Exception as e:
            st.error(f"Error fetching: {e}")
            break
            
    df = pd.DataFrame(all_candles, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
    df['Datetime'] = pd.to_datetime(df['Timestamp'], unit='ms')
    df.set_index('Datetime', inplace=True)
    df.drop(columns=['Timestamp', 'Volume'], inplace=True)
    
    status_text.text("Download Complete!")
    progress_bar.progress(100)
    return df

def download_yahoo_data(ticker, interval, period):
    """
    Tries to get max data from Yahoo.
    Note: 1m = max 7 days, 5m = max 60 days.
    """
    data = yf.download(ticker, period=period, interval=interval, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data

# ==========================================
# 2. STRATEGY LOGIC (Your 4 Patterns)
# ==========================================

def detect_bias(prev_candle, signal_candle):
    y_open, y_close = signal_candle['Open'], signal_candle['Close']
    y_high, y_low = signal_candle['High'], signal_candle['Low']
    x_high, x_low = prev_candle['High'], prev_candle['Low']
    
    is_bullish = y_close > y_open
    is_bearish = y_close < y_open

    # Pattern 1: Sweep & Reclaim/Reject
    if (y_low < x_low) and (y_close > x_low) and is_bullish:
        return 'Bullish', 'Pattern 1 (Sweep Reclaim)'
    if (y_high > x_high) and (y_close < x_high) and is_bearish:
        return 'Bearish', 'Pattern 1 (Sweep Reject)'

    # Pattern 2: Breakout & Hold
    if (y_high > x_high) and (y_close > x_high) and is_bullish:
        return 'Bullish', 'Pattern 2 (Breakout Hold)'
    if (y_low < x_low) and (y_close < x_low) and is_bearish:
        return 'Bearish', 'Pattern 2 (Breakout Hold)'

    return None, None

def run_backtest(df_intraday, asset_type):
    results = []
    
    # Resample to Daily for Bias
    df_daily = df_intraday.resample('1D').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'
    }).dropna()

    for i in range(2, len(df_daily)):
        day_exec = df_daily.index[i]
        
        # Determine Bias
        bias, pattern = detect_bias(df_daily.iloc[i-2], df_daily.iloc[i-1])
        
        if bias:
            # Zoom into Intraday
            day_data = df_intraday[df_intraday.index.date == day_exec.date()]
            if day_data.empty: continue

            # --- ENTRY RULES ---
            entry_candle = None
            
            if asset_type == "Indices (US30/NAS100)":
                # Check 09:50 - 10:10 NY Time
                # Note: Assuming Data is NY Time. 
                start_window = dt_time(9, 50)
                end_window = dt_time(10, 10)
                mask = (day_data.index.time >= start_window) & (day_data.index.time <= end_window)
                window_data = day_data[mask]
                if not window_data.empty:
                    entry_candle = window_data.iloc[0]
            
            else: # Crypto
                # Check 10:00 Time (Adjust for your timezone if needed)
                mask = (day_data.index.time >= dt_time(10, 0))
                window_data = day_data[mask]
                if not window_data.empty:
                    entry_candle = window_data.iloc[0]

            if entry_candle is not None:
                entry_price = entry_candle['Open']
                entry_time = entry_candle.name.time()
                
                # --- EXIT AT 12:00 ---
                trade_window = day_data[
                    (day_data.index.time >= entry_time) & 
                    (day_data.index.time <= dt_time(12, 0))
                ]
                
                if not trade_window.empty:
                    # Calculate Max Pips in Direction
                    if bias == 'Bullish':
                        max_move = trade_window['High'].max() - entry_price
                    else:
                        max_move = entry_price - trade_window['Low'].min()
                    
                    # Ensure non-negative (it can't be less than 0 favorable excursion)
                    max_move = max(0, max_move)
                    
                    results.append({
                        'Date': day_exec.date(),
                        'Bias': bias,
                        'Pattern': pattern,
                        'Entry Time': entry_time,
                        'Entry Price': entry_price,
                        'Max Pips Gained': round(max_move, 2)
                    })
                    
    return pd.DataFrame(results)

# ==========================================
# 3. USER INTERFACE
# ==========================================

st.set_page_config(page_title="Pro Data & Backtest", layout="wide")
st.title("🦅 Pro Backtest Lab: Download & Test")

# TABS
tab1, tab2 = st.tabs(["1. Download Data", "2. Run Backtest"])

# --- TAB 1: DOWNLOADER ---
with tab1:
    st.header("Get Historical Data")
    source = st.radio("Select Source:", ["Crypto (Binance - 3 Years FREE)", "Indices (Yahoo - Last 60 Days Only)"])
    
    if source == "Crypto (Binance - 3 Years FREE)":
        st.info("Uses CCXT to fetch 3 years of 5-minute data directly from Binance.")
        symbol = st.text_input("Symbol", "BTC/USDT")
        if st.button("Download 3 Years of BTC"):
            with st.spinner("Downloading... Do not close tab."):
                df = download_crypto_history(symbol, '5m', 1095) # 1095 days = 3 years
                df.to_csv("btc_3yr_data.csv")
                st.success(f"Downloaded {len(df)} candles! Saved as 'btc_3yr_data.csv'. Go to Tab 2.")
                
    else:
        st.warning("Yahoo Finance RESTRICTS 1-minute data to the last 7 days and 5-minute to 60 days.")
        ticker = st.text_input("Yahoo Ticker", "YM=F") # Dow Futures
        st.caption("Use 'YM=F' for US30 Futures, 'NQ=F' for Nas100 Futures.")
        if st.button("Download Last 60 Days"):
            df = download_yahoo_data(ticker, '5m', '60d')
            if not df.empty:
                df.to_csv("indices_data.csv")
                st.success(f"Downloaded {len(df)} candles. Saved as 'indices_data.csv'.")
            else:
                st.error("Download failed. Yahoo might be blocking requests.")

# --- TAB 2: BACKTESTER ---
with tab2:
    st.header("Upload & Analyze")
    
    # Settings
    col1, col2 = st.columns(2)
    with col1:
        asset_class = st.selectbox("Asset Class (Applies Logic)", ["Crypto (BTC)", "Indices (US30/NAS100)"])
    with col2:
        # Timezone adjuster
        tz_offset = st.number_input("Hour Offset (If Data is UTC, set -5 for NY)", value=0, step=1)

    uploaded_file = st.file_uploader("Drop your CSV file here (from Tab 1 or external)", type=['csv'])
    
    if uploaded_file:
        df_bt = pd.read_csv(uploaded_file)
        
        # Clean & Format
        df_bt.columns = [c.strip().title() for c in df_bt.columns]
        
        # Smart Date Parsing
        if 'Datetime' in df_bt.columns:
            df_bt['Datetime'] = pd.to_datetime(df_bt['Datetime'])
        elif 'Date' in df_bt.columns:
             df_bt['Datetime'] = pd.to_datetime(df_bt['Date'])
        
        df_bt.set_index('Datetime', inplace=True)
        
        # Apply Timezone Offset
        if tz_offset != 0:
            df_bt.index = df_bt.index + timedelta(hours=tz_offset)
            st.info(f"Applied {tz_offset} hour offset to data.")

        st.write(f"Analyzing {len(df_bt)} candles...")
        
        if st.button("Run Strategy"):
            results = run_backtest(df_bt, asset_class)
            
            if not results.empty:
                avg_pips = results['Max Pips Gained'].mean()
                
                m1, m2, m3 = st.columns(3)
                m1.metric("Total Setups", len(results))
                m2.metric("Avg Pips (Before 12:00)", f"{avg_pips:.1f}")
                m3.metric("Best Trade", f"{results['Max Pips Gained'].max():.1f}")
                
                st.bar_chart(results['Max Pips Gained'])
                st.dataframe(results)
            else:
                st.warning("No setups found. Check your data times.")