import pandas as pd
import pandas_ta as ta
import yfinance as yf
import pyupbit
import streamlit as st
from datetime import datetime
import numpy as np

# --- 설정 ---
ASSET_LIST = [
    {"name": "비트코인", "ticker": "KRW-BTC", "source": "upbit", "category": "코인"},
    {"name": "테더 (USDT)", "ticker": "KRW-USDT", "source": "upbit", "category": "스테이블 코인"},
    {"name": "SPY", "ticker": "SPY", "source": "yahoo", "category": "ETF"},
    {"name": "QQQ", "ticker": "QQQ", "source": "yahoo", "category": "ETF"},
    {"name": "TQQQ", "ticker": "TQQQ", "source": "yahoo", "category": "ETF"},
    {"name": "금 선물", "ticker": "GC=F", "source": "yahoo", "category": "선물"},
    {"name": "달러 선물", "ticker": "DX=F", "source": "yahoo", "category": "선물"},
]

# 전략에서 지정한 타임프레임
INTERVALS = ["5분", "15분"]

# --- 수수료 설정 (단방향 기준) ---
FEE_RATES = {
    "upbit": 0.0005,      # 0.05%
    "yahoo_etf": 0.0025,  # 0.25% (일반적인 해외주식 수수료)
    "yahoo_future": 0.0005 # 0.05% (선물/CFD 등 가정)
}

# --- 데이터 수집 함수 (캐싱 적용) ---
@st.cache_data(ttl=3600, show_spinner=False)
def get_data(ticker, source, interval_str):
    df = pd.DataFrame()
    try:
        upbit_int_map = {"5분":"minute5", "15분":"minute15", "30분":"minute30", "1시간":"minute60", "4시간":"minute240", "1일":"day"}
        yahoo_int_map = {"5분":"5m", "15분":"15m", "30분":"30m", "1시간":"1h", "4시간":"1h", "1일":"1d"}
        
        req_count = 2000 
        
        if source == "upbit":
            target_interval = upbit_int_map.get(interval_str, "day")
            df = pyupbit.get_ohlcv(ticker, interval=target_interval, count=req_count)
        
        elif source == "yahoo":
            target_interval = yahoo_int_map.get(interval_str, "1d")
            target_period = "1mo" if target_interval in ["5m", "15m", "30m"] else "2y"
            
            df = yf.download(ticker, period=target_period, interval=target_interval, progress=False, auto_adjust=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
                else:
                    df.columns = [c.lower() for c in df.columns]
                cols = ['open','high','low','close','volume']
                df = df[[c for c in cols if c in df.columns]]
                
    except Exception as e:
        print(f"Error fetching {ticker}: {e}")
    
    return df

# --- 보조 함수: W패턴 확인 ---
def check_double_bottom(series, idx, window=20, tolerance=0.005):
    if idx < window: return False
    current_low = series.iloc[idx]
    past_window = series.iloc[idx-window : idx]
    similar_bottoms = past_window[abs(past_window - current_low) / current_low <= tolerance]
    
    if len(similar_bottoms) > 0:
        first_bottom_idx = similar_bottoms.index[-1]
        try:
             first_bottom_pos = series.index.get_loc(first_bottom_idx)
             if first_bottom_pos < idx:
                 middle_high = series.iloc[first_bottom_pos : idx].max()
                 if middle_high > current_low * 1.005:
                     return True
        except KeyError:
            return False
    return False

# --- [v1] 하일수 하이브리드 전략 (Basic) ---
def run_hybrid_strategy_v1(df):
    if df is None or df.empty or len(df) < 202: return None
    df = df.copy()
    
    df['RSI'] = ta.rsi(df['close'], length=14)
    bb = ta.bbands(df['close'], length=20, std=2)
    
    bbu_col = [c for c in bb.columns if c.startswith('BBU')][0]
    bbl_col = [c for c in bb.columns if c.startswith('BBL')][0]
    bbm_col = [c for c in bb.columns if c.startswith('BBM')][0]
    
    df['BB_Upper'] = bb[bbu_col]
    df['BB_Lower'] = bb[bbl_col]
    df['BB_Mid'] = bb[bbm_col]
    df['SMA_202'] = ta.sma(df['close'], length=202)

    balance = 1000000
    initial_balance = 1000000
    position = None 
    entry_price = 0
    trades = []
    equity_curve = [] 
    
    TARGET_PROFIT = 0.01 
    STOP_LOSS = 0.10     
    rescue_mode = False 
    
    for i in range(len(df)):
        equity_curve.append({'time': df.index[i], 'balance': balance})
        if i < 202: continue
        
        curr_close = df['close'].iloc[i]
        prev_close = df['close'].iloc[i-1]
        curr_time = str(df.index[i])
        curr_rsi = df['RSI'].iloc[i]
        bb_lower = df['BB_Lower'].iloc[i]
        prev_bb_lower = df['BB_Lower'].iloc[i-1]
        
        # Exit
        if position == 'long':
            pnl = (curr_close - entry_price) / entry_price
            is_close = False
            reason = ""
            
            if pnl >= TARGET_PROFIT:
                is_close = True; reason = "Target 1% Reached"; rescue_mode = False
            elif pnl <= -STOP_LOSS:
                is_close = True; reason = "Stop Loss (-10%)"; rescue_mode = False
            elif curr_rsi > 70:
                if pnl > 0: is_close = True; reason = "RSI > 70 Profit"; rescue_mode = False
                else: rescue_mode = True
            
            if rescue_mode and pnl >= 0:
                is_close = True; reason = "Rescue Exit (Breakeven)"; rescue_mode = False
            
            if is_close:
                balance *= (1 + pnl)
                trades.append({'time': curr_time, 'type': 'Exit', 'pnl': pnl, 'reason': reason, 'price': curr_close, 'balance': balance})
                position = None
                
        # Entry
        if position is None:
            band_reversal = (prev_close < prev_bb_lower) and (curr_close > bb_lower)
            rsi_condition = (curr_rsi < 35) 
            
            if band_reversal and rsi_condition:
                position = 'long'
                entry_price = curr_close
                trades.append({'time': curr_time, 'type': 'Entry', 'reason': "Band Reversal + RSI < 35", 'price': curr_close, 'balance': balance})

    total_exits = [t for t in trades if t['type'] == 'Exit']
    win_trades = [t for t in total_exits if t['pnl'] > 0]
    win_rate = (len(win_trades) / len(total_exits) * 100) if total_exits else 0
    total_return = (balance - initial_balance) / initial_balance * 100
    
    return {
        "return": total_return,
        "win_rate": win_rate,
        "trades": len(total_exits),
        "trade_history": trades,
        "equity_curve": equity_curve,
        "last_price": df['close'].iloc[-1]
    }

# --- [v2] 하일수 하이브리드 전략 (Optimized) ---
def run_hybrid_strategy_v2(df):
    if df is None or df.empty or len(df) < 202: return None
    df = df.copy()
    
    df['RSI'] = ta.rsi(df['close'], length=14)
    bb1 = ta.bbands(df['close'], length=20, std=2)
    bbu1_col = [c for c in bb1.columns if c.startswith('BBU')][0]
    bbl1_col = [c for c in bb1.columns if c.startswith('BBL')][0]
    bbm1_col = [c for c in bb1.columns if c.startswith('BBM')][0]
    
    df['BB1_Upper'] = bb1[bbu1_col]
    df['BB1_Lower'] = bb1[bbl1_col]
    df['BB1_Mid'] = bb1[bbm1_col]
    df['SMA_202'] = ta.sma(df['close'], length=202)

    balance = 1000000
    initial_balance = 1000000
    position = None 
    entry_price = 0
    trades = []
    equity_curve = [] 
    
    TARGET_PROFIT = 0.01 
    STOP_LOSS = 0.10      
    rescue_mode = False 
    
    for i in range(len(df)):
        equity_curve.append({'time': df.index[i], 'balance': balance})
        if i < 202: continue
        
        curr_close = df['close'].iloc[i]
        prev_close = df['close'].iloc[i-1]
        curr_time = str(df.index[i])
        curr_rsi = df['RSI'].iloc[i]
        bb1_lower = df['BB1_Lower'].iloc[i]
        prev_bb1_lower = df['BB1_Lower'].iloc[i-1]
        bb1_mid = df['BB1_Mid'].iloc[i]
        sma_202 = df['SMA_202'].iloc[i]
        
        # Exit
        if position == 'long':
            pnl = (curr_close - entry_price) / entry_price
            is_close = False; reason = ""
            
            if pnl >= TARGET_PROFIT:
                is_close = True; reason = "Target 1% Reached"; rescue_mode = False 
            elif pnl <= -STOP_LOSS:
                is_close = True; reason = "Stop Loss (-10%)"; rescue_mode = False
            elif curr_rsi >= 70:
                if pnl > 0: is_close = True; reason = "RSI > 70 Profit"; rescue_mode = False
                else: rescue_mode = True
            elif curr_close >= bb1_mid and pnl > 0.005:
                 is_close = True; reason = "BB Mid Touch Profit"; rescue_mode = False

            if rescue_mode and pnl >= 0:
                is_close = True; reason = "Rescue Exit (Breakeven)"; rescue_mode = False
            
            if is_close:
                balance *= (1 + pnl)
                trades.append({'time': curr_time, 'type': 'Exit', 'pnl': pnl, 'reason': reason, 'price': curr_close, 'balance': balance})
                position = None
                
        # Entry
        if position is None:
            rsi_condition = (curr_rsi <= 30)
            band_reversal = (prev_close < prev_bb1_lower) and (curr_close > bb1_lower)
            w_pattern = check_double_bottom(df['low'], i)
            dist_to_sma202 = (curr_close - sma_202) / sma_202
            sma202_support = (dist_to_sma202 > 0) and (dist_to_sma202 < 0.015)
            
            entry_signal = False; entry_reason = ""
            
            if band_reversal and rsi_condition:
                entry_signal = True; entry_reason = "Band Reversal + RSI <= 30"
            elif w_pattern and rsi_condition:
                entry_signal = True; entry_reason = "W-Pattern + RSI <= 30"
            elif sma202_support and rsi_condition:
                entry_signal = True; entry_reason = "202 SMA Support + RSI <= 30"

            if entry_signal:
                position = 'long'
                entry_price = curr_close
                trades.append({'time': curr_time, 'type': 'Entry', 'reason': entry_reason, 'price': curr_close, 'balance': balance})

    total_exits = [t for t in trades if t['type'] == 'Exit']
    win_trades = [t for t in total_exits if t['pnl'] > 0]
    win_rate = (len(win_trades) / len(total_exits) * 100) if total_exits else 0
    total_return = (balance - initial_balance) / initial_balance * 100
    
    return {
        "return": total_return,
        "win_rate": win_rate,
        "trades": len(total_exits),
        "trade_history": trades,
        "equity_curve": equity_curve,
        "last_price": df['close'].iloc[-1]
    }

# --- [v3] 하일수 하이브리드 전략 (Target 2%) ---
def run_hybrid_strategy_v3(df):
    if df is None or df.empty or len(df) < 202: return None
    df = df.copy()
    
    df['RSI'] = ta.rsi(df['close'], length=14)
    bb1 = ta.bbands(df['close'], length=20, std=2)
    bbu1_col = [c for c in bb1.columns if c.startswith('BBU')][0]
    bbl1_col = [c for c in bb1.columns if c.startswith('BBL')][0]
    bbm1_col = [c for c in bb1.columns if c.startswith('BBM')][0]
    
    df['BB1_Upper'] = bb1[bbu1_col]
    df['BB1_Lower'] = bb1[bbl1_col]
    df['BB1_Mid'] = bb1[bbm1_col]
    df['SMA_202'] = ta.sma(df['close'], length=202)

    balance = 1000000
    initial_balance = 1000000
    position = None 
    entry_price = 0
    trades = []
    equity_curve = [] 
    
    TARGET_PROFIT = 0.02 # 목표 수익률 2%로 상향
    STOP_LOSS = 0.10      
    rescue_mode = False 
    
    for i in range(len(df)):
        equity_curve.append({'time': df.index[i], 'balance': balance})
        if i < 202: continue
        
        curr_close = df['close'].iloc[i]
        prev_close = df['close'].iloc[i-1]
        curr_time = str(df.index[i])
        curr_rsi = df['RSI'].iloc[i]
        bb1_lower = df['BB1_Lower'].iloc[i]
        prev_bb1_lower = df['BB1_Lower'].iloc[i-1]
        bb1_mid = df['BB1_Mid'].iloc[i]
        sma_202 = df['SMA_202'].iloc[i]
        
        # Exit
        if position == 'long':
            pnl = (curr_close - entry_price) / entry_price
            is_close = False; reason = ""
            
            if pnl >= TARGET_PROFIT:
                is_close = True; reason = "Target 2% Reached"; rescue_mode = False 
            elif pnl <= -STOP_LOSS:
                is_close = True; reason = "Stop Loss (-10%)"; rescue_mode = False
            elif curr_rsi >= 70:
                if pnl > 0: is_close = True; reason = "RSI > 70 Profit"; rescue_mode = False
                else: rescue_mode = True
            elif curr_close >= bb1_mid and pnl > 0.005:
                 is_close = True; reason = "BB Mid Touch Profit"; rescue_mode = False

            if rescue_mode and pnl >= 0:
                is_close = True; reason = "Rescue Exit (Breakeven)"; rescue_mode = False
            
            if is_close:
                balance *= (1 + pnl)
                trades.append({'time': curr_time, 'type': 'Exit', 'pnl': pnl, 'reason': reason, 'price': curr_close, 'balance': balance})
                position = None
                
        # Entry
        if position is None:
            rsi_condition = (curr_rsi <= 30)
            band_reversal = (prev_close < prev_bb1_lower) and (curr_close > bb1_lower)
            w_pattern = check_double_bottom(df['low'], i)
            dist_to_sma202 = (curr_close - sma_202) / sma_202
            sma202_support = (dist_to_sma202 > 0) and (dist_to_sma202 < 0.015)
            
            entry_signal = False; entry_reason = ""
            
            if band_reversal and rsi_condition:
                entry_signal = True; entry_reason = "Band Reversal + RSI <= 30"
            elif w_pattern and rsi_condition:
                entry_signal = True; entry_reason = "W-Pattern + RSI <= 30"
            elif sma202_support and rsi_condition:
                entry_signal = True; entry_reason = "202 SMA Support + RSI <= 30"

            if entry_signal:
                position = 'long'
                entry_price = curr_close
                trades.append({'time': curr_time, 'type': 'Entry', 'reason': entry_reason, 'price': curr_close, 'balance': balance})

    total_exits = [t for t in trades if t['type'] == 'Exit']
    win_trades = [t for t in total_exits if t['pnl'] > 0]
    win_rate = (len(win_trades) / len(total_exits) * 100) if total_exits else 0
    total_return = (balance - initial_balance) / initial_balance * 100
    
    return {
        "return": total_return,
        "win_rate": win_rate,
        "trades": len(total_exits),
        "trade_history": trades,
        "equity_curve": equity_curve,
        "last_price": df['close'].iloc[-1]
    }

def get_d1_analysis(progress_callback=None):
    results = []
    total_steps = len(ASSET_LIST) * len(INTERVALS)
    current_step = 0
    
    strategies = [
        {"name": "Hybrid v1", "func": run_hybrid_strategy_v1},
        {"name": "Hybrid v2", "func": run_hybrid_strategy_v2},
        {"name": "Hybrid v3 (2%)", "func": run_hybrid_strategy_v3}
    ]
    
    for asset in ASSET_LIST:
        for interval in INTERVALS:
            if progress_callback:
                progress_callback(current_step, total_steps, f"[{asset['name']}] {interval} 분석 중...")
                
            df = get_data(asset['ticker'], asset['source'], interval)
            
            for strat in strategies:
                res = strat["func"](df)
                if res:
                    results.append({
                        "asset": asset['name'],
                        "ticker": asset['ticker'],
                        "source": asset['source'],
                        "category": asset['category'],
                        "interval": interval,
                        "strategy": strat["name"],
                        "timestamp": datetime.now().isoformat(),
                        **res
                    })
            current_step += 1
            
    if progress_callback:
        progress_callback(total_steps, total_steps, "완료")
        
    return results
