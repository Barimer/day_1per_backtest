import streamlit as st
import pandas as pd
from datetime import datetime
import d1_analyzer
import altair as alt

# --- 페이지 설정 ---
st.set_page_config(layout="wide", page_title="Hybrid Strategy Dashboard", page_icon="📈")

# --- CSS 스타일링 ---
st.markdown("""
<style>
    .stDataFrame { font-size: 14px; }
    .metric-card {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
</style>
""", unsafe_allow_html=True)

# --- 데이터 로드 ---
def load_data(progress_bar, status_text):
    def update_progress(current, total, message):
        percent = current / total
        if percent > 1.0: percent = 1.0
        progress_bar.progress(percent)
        status_text.text(f"진행률: {int(percent * 100)}% - {message}")

    raw_data = d1_analyzer.get_d1_analysis(progress_callback=update_progress)
    return pd.DataFrame(raw_data)

def main():
    st.title("📈 하일수 하이브리드 전략 대시보드")
    st.caption(f"마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    st.markdown("""
    > **전략 핵심 (Hybrid Optimization)**: 
    > * **진입**: RSI < 35 (과매도) + **밴드 회귀 (Band Reversal)**
    > * **청산**: **+1% 익절 (최우선)** / -10% 손절
    > * **구조대 (Rescue)**: RSI > 70인데 손실 중이면? **본절(0%) 올 때까지 버티기**
    """)
    
    with st.expander("ℹ️ **봉 길이(Interval) 선택 이유?**"):
        st.markdown("""
        전략 명세서에 따르면 **"1시간봉으로 추세를 보고, 5분/15분봉으로 타점을 잡는다"**고 되어 있습니다.
        *   **5분봉**: 더 민감하게 반응하여 진입 기회가 많지만, 속임수(휩소)에 당할 확률도 높습니다.
        *   **15분봉**: 5분봉보다 신뢰도가 높지만, 진입 기회가 적을 수 있습니다.
        *   이 대시보드에서는 두 타임프레임 중 **어느 것이 더 수익률이 좋은지 비교**하기 위해 선택 옵션을 제공합니다.
        """)

    col_btn, col_dummy = st.columns([1, 5])
    with col_btn:
        start_btn = st.button("🔄 데이터 분석 시작", type="primary")
        
    status_text = st.empty()
    progress_bar = st.empty()
    
    # 데이터 로드 로직
    if start_btn:
        st.cache_data.clear()
        df = load_data(progress_bar, status_text)
        st.session_state['df'] = df
        st.session_state['data_loaded'] = True
        st.rerun()

    if 'data_loaded' not in st.session_state:
        st.info("위의 '데이터 분석 시작' 버튼을 눌러 분석을 시작하세요.")
    else:
        df = st.session_state['df']
        
        # --- 사이드바 필터 ---
        # --- 사이드바 필터 (Form) ---
        with st.sidebar.form(key='filter_form'):
            st.header("🔍 필터 설정")
            
            def multiselect_checkbox(label, options, key_prefix):
                st.write(f"**{label}**")
                selected = []
                # 옵션이 많으면 Expander로 숨김
                with st.expander(f"{label} 선택", expanded=False):
                    # 전체 선택/해제 기능은 복잡해지므로 생략하고 개별 선택만 구현
                    for item in options:
                        # 기본값: True (모두 선택)
                        if st.checkbox(str(item), value=True, key=f"{key_prefix}_{item}"):
                            selected.append(item)
                return selected

            # 1. 전략 필터
            strategies = list(df['strategy'].unique())
            sel_strategies = multiselect_checkbox("전략 버전", strategies, "strat")

            # 2. 자산 필터
            assets = list(df['asset'].unique())
            sel_assets = multiselect_checkbox("자산", assets, "asset")
            
            # 3. 봉 길이 필터
            intervals = list(df['interval'].unique())
            sel_intervals = multiselect_checkbox("봉 길이", intervals, "int")
            
            # 4. 기간(월별) 필터
            all_trades = []
            for _, row in df.iterrows():
                if row['trade_history']:
                    for t in row['trade_history']:
                        all_trades.append(t)
            
            if all_trades:
                trade_dates = [pd.to_datetime(t['time']) for t in all_trades]
                months = sorted(list(set([d.strftime("%Y-%m") for d in trade_dates])), reverse=True)
            else:
                months = []
                
            sel_months = multiselect_checkbox("월(Month)", months, "month")
            
            submit_button = st.form_submit_button("적용")

        # 필터 상태 저장 (버튼 누를 때만 업데이트)
        if submit_button:
            st.session_state['filter_strategies'] = sel_strategies
            st.session_state['filter_assets'] = sel_assets
            st.session_state['filter_intervals'] = sel_intervals
            st.session_state['filter_months'] = sel_months
            st.session_state['filters_applied'] = True

        # 초기 로드 시 기본값 설정
        if 'filters_applied' not in st.session_state:
            st.session_state['filter_strategies'] = strategies
            st.session_state['filter_assets'] = assets
            st.session_state['filter_intervals'] = intervals
            st.session_state['filter_months'] = [] # 빈 리스트는 전체 기간 의미
            st.session_state['filters_applied'] = True

        # --- 필터링 및 재계산 로직 ---
        
        # 저장된 필터 값 사용
        target_strategies = st.session_state['filter_strategies']
        target_assets = st.session_state['filter_assets']
        target_intervals = st.session_state['filter_intervals']
        target_months = st.session_state['filter_months']

        # 기본 필터링 (전략, 자산, 봉길이)
        filtered_df = df.copy()
        
        if target_strategies:
            filtered_df = filtered_df[filtered_df['strategy'].isin(target_strategies)]
        else:
            filtered_df = filtered_df.iloc[0:0]

        if target_assets:
            filtered_df = filtered_df[filtered_df['asset'].isin(target_assets)]
        else:
            filtered_df = filtered_df.iloc[0:0]

        if target_intervals:
            filtered_df = filtered_df[filtered_df['interval'].isin(target_intervals)]
        else:
            filtered_df = filtered_df.iloc[0:0]
        
        # 재계산된 결과를 담을 리스트
        recalculated_results = []
        
        initial_capital = 1000000
        
        total_initial = 0
        total_final_no_fee = 0
        total_final_with_fee = 0
        total_trades_count = 0
        total_wins = 0
        
        # 수수료율 정보를 수집하기 위한 세트
        applied_fee_rates = set()

        for _, row in filtered_df.iterrows():
            trades = row['trade_history']
            
            # 수수료율 결정
            source = row.get('source', 'upbit') # 기존 데이터 호환성
            category = row.get('category', '코인')
            
            if source == 'upbit':
                fee_rate = d1_analyzer.FEE_RATES['upbit']
            elif source == 'yahoo':
                if category == 'ETF':
                    fee_rate = d1_analyzer.FEE_RATES['yahoo_etf']
                else:
                    fee_rate = d1_analyzer.FEE_RATES['yahoo_future']
            else:
                fee_rate = 0.001 # 기본값
            
            applied_fee_rates.add(fee_rate)

            # 월별 필터링 (멀티 선택)
            if target_months:
                trades = [t for t in trades if pd.to_datetime(t['time']).strftime("%Y-%m") in target_months]
            
            # 해당 자산/기간의 성과 계산
            balance_no_fee = initial_capital
            balance_with_fee = initial_capital
            wins = 0
            
            valid_trades = [] # 필터링된 거래만 담음
            
            for t in trades:
                if t['type'] == 'Exit':
                    pnl = t['pnl']
                    balance_no_fee *= (1 + pnl)
                    # 매수/매도 각각 수수료 적용 (간략화: 수익률에서 2배 차감 근사치 대신, 정확히 자산에서 차감)
                    # 진입 시 수수료: balance * (1 - fee)
                    # 청산 시 수수료: balance * (1 - fee)
                    # 여기서는 PnL 계산 후 한 번에 적용하는 방식 유지하되, 왕복 고려
                    
                    # 방법 1: PnL에서 수수료 차감 (단순화)
                    # net_pnl = pnl - (fee_rate * 2) 
                    # balance_with_fee *= (1 + net_pnl)
                    
                    # 방법 2: 자산 자체 차감 (더 정확)
                    # 진입: trade_amt = balance * (1 - fee)
                    # 청산: result_amt = trade_amt * (1 + pnl) * (1 - fee)
                    # result_amt = balance * (1 - fee) * (1 + pnl) * (1 - fee)
                    # result_amt = balance * (1 + pnl) * (1 - fee)^2
                    
                    balance_with_fee *= (1 + pnl) * ((1 - fee_rate) ** 2)

                    if pnl > 0: wins += 1
                    valid_trades.append(t)
                elif t['type'] == 'Entry':
                        valid_trades.append(t)

            trade_count = len([t for t in valid_trades if t['type'] == 'Exit'])
            
            if trade_count > 0:
                win_rate = (wins / trade_count) * 100
                ret_no_fee = (balance_no_fee - initial_capital) / initial_capital * 100
                ret_with_fee = (balance_with_fee - initial_capital) / initial_capital * 100
                
                recalculated_results.append({
                    'strategy': row['strategy'], # strategy 컬럼 추가
                    'asset': row['asset'],
                    'interval': row['interval'],
                    'trades': trade_count,
                    'win_rate': win_rate,
                    'return': ret_no_fee,
                    'return_fee': ret_with_fee,
                    'final_balance': balance_with_fee,
                    'trade_history': valid_trades,
                    'last_price': row['last_price']
                })
                
                total_initial += initial_capital
                total_final_no_fee += balance_no_fee
                total_final_with_fee += balance_with_fee
                total_trades_count += trade_count
                total_wins += wins
        
        # 결과 DataFrame 생성
        result_df = pd.DataFrame(recalculated_results)
        
        # --- 결과 표시 ---
        month_str = ", ".join(target_months) if target_months else "All"
        st.subheader(f"📊 분석 결과 ({month_str})")
            
        # 전체 통계
        if total_initial > 0:
            avg_win_rate = (total_wins / total_trades_count * 100) if total_trades_count > 0 else 0
            total_return_no_fee = (total_final_no_fee - total_initial) / total_initial * 100
            total_return_with_fee = (total_final_with_fee - total_initial) / total_initial * 100
            total_fee_paid = total_final_no_fee - total_final_with_fee
        else:
            avg_win_rate = 0
            total_return_no_fee = 0
            total_return_with_fee = 0
            total_fee_paid = 0
            total_final_with_fee = 0

        # 100만원 기준 정규화 (사용자 요청)
        display_initial = 1000000
        
        # 수수료 전 계산
        display_final_no_fee = display_initial * (1 + total_return_no_fee / 100)
        display_profit_no_fee = display_final_no_fee - display_initial
        
        # 수수료 후 계산
        display_final_with_fee = display_initial * (1 + total_return_with_fee / 100)
        display_profit_with_fee = display_final_with_fee - display_initial
        
        # 총 수수료 비용
        display_fee = display_final_no_fee - display_final_with_fee

        # 수수료율 표시 문자열 생성
        if not applied_fee_rates:
            fee_str = "N/A"
        elif len(applied_fee_rates) == 1:
            rate = list(applied_fee_rates)[0]
            fee_str = f"{rate*100:.2f}% (왕복 {rate*200:.2f}%)"
        else:
            min_rate = min(applied_fee_rates)
            max_rate = max(applied_fee_rates)
            fee_str = f"{min_rate*100:.2f}% ~ {max_rate*100:.2f}% (혼합)"

        # --- 결과 표시 ---
        # 1. 선택한 조건의 백테스팅 결과
        st.subheader("📊 선택한 조건의 백테스팅 결과")
        col1, col2, col3, col4 = st.columns(4)
        
        col1.metric("총 거래 횟수", f"{total_trades_count} 회")
        col2.metric("평균 승률", f"{avg_win_rate:.1f}%")
        col3.metric("수익률 (수수료 전)", f"{total_return_no_fee:.2f}%", f"{total_return_no_fee:.2f}%")
        col4.metric("최종 금액 (수수료 전)", f"{int(display_final_no_fee):,} 원", f"{int(display_profit_no_fee):,} 원")

        st.markdown("---")

        # 2. 수수료 적용 후 실제 수익
        st.subheader("💰 수수료 적용 후 실제 수익")
        col5, col6, col7, col8 = st.columns(4)
        
        col5.metric("총 수수료 비용", f"{int(display_fee):,} 원", f"-{display_fee/display_initial*100:.2f}%")
        col6.metric("적용 수수료율 (편도)", fee_str)
        col7.metric("실제 수익률 (수수료 후)", f"{total_return_with_fee:.2f}%", f"{total_return_with_fee:.2f}%")
        col8.metric("실제 최종 금액 (수수료 후)", f"{int(display_final_with_fee):,} 원", f"{int(display_profit_with_fee):,} 원")
        
        st.divider()
        
        # --- 상세 테이블 ---
        if not result_df.empty:
            st.subheader("📋 자산별 성과 (수수료 적용)")
            
            display_cols = ['strategy', 'asset', 'interval', 'return_fee', 'win_rate', 'trades', 'final_balance']
            display_df = result_df[display_cols].sort_values(by='return_fee', ascending=False)
            
            def color_return(val):
                color = '#4CAF50' if val > 0 else '#FF5252' if val < 0 else 'white'
                return f'color: {color}; font-weight: bold;'
                
            st.dataframe(
                display_df.style.applymap(color_return, subset=['return_fee'])
                .format({
                    'return_fee': "{:.2f}%", 
                    'win_rate': "{:.1f}%", 
                    'final_balance': "{:,.0f}"
                }),
                use_container_width=True
            )
            
            # --- 상세 분석 (Expanders) ---
            st.subheader("📝 상세 거래 기록")
                
            for i, row in result_df.iterrows():
                with st.expander(f"{row['asset']} ({row['interval']}) - 수익률: {row['return_fee']:.2f}%"):
                    
                    if row['trade_history']:
                        history_df = pd.DataFrame(row['trade_history'])
                        
                        # 컬럼 정리
                        cols_order = ['time', 'type', 'reason', 'price', 'pnl']
                        history_df = history_df[[c for c in cols_order if c in history_df.columns]]
                        
                        # 스타일링 적용
                        styler = history_df.style.format({'price': "{:,.2f}"})
                        
                        if 'pnl' in history_df.columns:
                            styler = styler.applymap(lambda x: 'color: #4CAF50; font-weight: bold;' if x>0 else 'color: #FF5252; font-weight: bold;' if x<0 else '', subset=['pnl']).format({'pnl': "{:.2%}"})
                        
                        st.dataframe(styler, use_container_width=True)
                    else:
                        st.info("선택된 기간에 거래 기록이 없습니다.")
        else:
            st.warning("선택한 조건에 맞는 거래 데이터가 없습니다.")

if __name__ == "__main__":
    main()
