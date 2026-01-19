import streamlit as st
import yfinance as yf
import pandas_ta as ta
import google.generativeai as genai
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime

# --- 頁面設定 ---
st.set_page_config(page_title="AI 美股技術分析戰情室", page_icon="📈", layout="wide")

# --- 側邊欄：API 設定 ---
with st.sidebar:
    st.header("⚙️ 系統設定")
    if 'GEMINI_API_KEY' in st.secrets:
        GEMINI_API_KEY = st.secrets['GEMINI_API_KEY']
        st.success("API Key 已載入")
    else:
        GEMINI_API_KEY = st.text_input("輸入 Gemini API Key", type="password")

# --- 您的策略邏輯 (作為 AI 的系統提示詞) ---
STRATEGY_CONTEXT = """
你是專業的美股技術分析師。請嚴格根據以下策略邏輯進行分析，不要使用外部不明確的指標。

【技術指標規則】
1. K線型態：
   - 買進：低檔長下影線(錘子)、實體大紅K(無上影)、W底/頭肩底突破。
   - 賣出：高檔長上影線(射擊之星)、實體大黑K(無下影)、M頭/頭肩頂跌破。
2. 價量關係：
   - 價漲量增：多頭健康 (買)。
   - 價漲量縮：追價意願低 (賣/風險)。
3. KD指標：
   - 黃金交叉 (K向上穿過D) 且數值 < 20：強烈買訊。
   - 死亡交叉 (K向下跌破D) 且數值 > 80：強烈賣訊。
4. 布林通道 (20MA, 2std)：
   - 買進：跌破下軌後收紅K重回軌道 (超賣)，或布林張口且帶量突破上軌。
   - 賣出：觸及上軌出現反轉訊號，或跌破中軌。
   - 擠壓 (Squeeze)：帶寬變窄預示大行情。
5. RSI：參考輔助，低於 30 為超賣，高於 70 為超買。

【你的任務】
根據提供的數據，給出：
1. **趨勢分析**：綜合 K 線、布林、KD、成交量判斷目前趨勢。
2. **具體建議**：明確指出是「觀望」、「買進佈局」還是「減碼賣出」。
3. **關鍵價格**：
   - **建議買入價**：基於支撐位或突破點。
   - **建議停損價**：基於前低或布林下軌/中軌。
4. **未來發展預測**：簡述原因。
"""

# --- 核心函式 ---

def get_stock_data(symbol):
    try:
        # 下載 1 年數據以確保指標計算準確
        df = yf.download(symbol, period="1y", interval="1d", progress=False)
        if df.empty: return None
        
        # 處理 MultiIndex (yfinance 新版問題)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        # 計算指標
        # 1. RSI
        df['RSI'] = ta.rsi(df['Close'], length=14)
        # 2. 布林通道
        bb = ta.bbands(df['Close'], length=20, std=2)
        df = pd.concat([df, bb], axis=1)
        # 3. KD (Stochastics)
        stoch = ta.stoch(df['High'], df['Low'], df['Close'], k=9, d=3)
        df = pd.concat([df, stoch], axis=1)
        
        # 重新命名方便存取
        df.rename(columns={
            'BBL_20_2.0': 'BB_Lower', 
            'BBM_20_2.0': 'BB_Mid', 
            'BBU_20_2.0': 'BB_Upper',
            'STOCHk_9_3_3': 'K',
            'STOCHd_9_3_3': 'D'
        }, inplace=True)
        
        return df.tail(120) # 只回傳最近半年供繪圖
    except Exception as e:
        st.error(f"數據抓取失敗: {e}")
        return None

def plot_interactive_chart(df, symbol):
    # 建立子圖：主圖(K線+布林), 成交量, KD/RSI
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, 
                        vertical_spacing=0.05, 
                        row_heights=[0.6, 0.2, 0.2],
                        subplot_titles=(f'{symbol} 日線圖 (布林通道)', '成交量', 'KD指標'))

    # 1. 主圖：K線 (紅漲綠跌)
    fig.add_trace(go.Candlestick(x=df.index,
                    open=df['Open'], high=df['High'],
                    low=df['Low'], close=df['Close'],
                    name='K線',
                    increasing_line_color='red', decreasing_line_color='green'), row=1, col=1)

    # 布林通道線
    fig.add_trace(go.Scatter(x=df.index, y=df['BB_Upper'], line=dict(color='gray', width=1), name='上軌'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['BB_Mid'], line=dict(color='orange', width=1), name='中軌(20MA)'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['BB_Lower'], line=dict(color='gray', width=1), name='下軌'), row=1, col=1)

    # 2. 成交量 (顏色跟隨漲跌)
    colors = ['red' if row['Open'] < row['Close'] else 'green' for i, row in df.iterrows()]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=colors, name='成交量'), row=2, col=1)

    # 3. KD 指標
    fig.add_trace(go.Scatter(x=df.index, y=df['K'], line=dict(color='blue', width=1.5), name='K值'), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['D'], line=dict(color='orange', width=1.5), name='D值'), row=3, col=1)
    # 畫出 80/20 參考線
    fig.add_hline(y=80, line_dash="dash", line_color="gray", row=3, col=1)
    fig.add_hline(y=20, line_dash="dash", line_color="gray", row=3, col=1)

    fig.update_layout(height=800, xaxis_rangeslider_visible=False, title_text=f"{symbol} 技術分析圖表")
    return fig

def ask_gemini_analysis(symbol, df):
    if not GEMINI_API_KEY: return "請先輸入 API Key"
    
    # 提取最新一筆數據
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 構建數據摘要
    data_summary = f"""
    【{symbol} 最新數據 ({last.name.date()})】
    - 收盤價: {last['Close']:.2f} (前日: {prev['Close']:.2f})
    - 成交量: {int(last['Volume'])} (前日: {int(prev['Volume'])})
    - RSI(14): {last['RSI']:.2f}
    - KD指標: K={last['K']:.2f}, D={last['D']:.2f} (前日 K={prev['K']:.2f}, D={prev['D']:.2f})
    - 布林通道: 上軌={last['BB_Upper']:.2f}, 中軌={last['BB_Mid']:.2f}, 下軌={last['BB_Lower']:.2f}
    - 價格位置: 距離下軌 {(last['Close'] - last['BB_Lower']):.2f}, 距離上軌 {(last['BB_Upper'] - last['Close']):.2f}
    """

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    try:
        response = model.generate_content(STRATEGY_CONTEXT + "\n\n" + data_summary)
        return response.text
    except Exception as e:
        return f"AI 分析錯誤: {e}"

# --- 主介面 ---
st.title("📈 AI 美股技術分析：日線級別")
st.markdown("結合 **K線型態、布林通道、KD、RSI** 與 **成交量** 的全方位健診系統。")

col1, col2 = st.columns([3, 1])
with col1:
    symbol = st.text_input("請輸入美股代號 (例如: TSLA, NVDA, AAPL)", value="TSLA").upper()
with col2:
    analyze_btn = st.button("🚀 開始分析", type="primary", use_container_width=True)

if analyze_btn and symbol:
    with st.spinner(f"正在抓取 {symbol} 數據並計算指標..."):
        df = get_stock_data(symbol)
        
        if df is not None:
            # 1. 顯示互動圖表
            st.plotly_chart(plot_interactive_chart(df, symbol), use_container_width=True)
            
            # 2. 顯示最新數據快照
            last_row = df.iloc[-1]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("RSI (14)", f"{last_row['RSI']:.1f}", delta_color="off")
            c2.metric("K值 (9)", f"{last_row['K']:.1f}")
            c3.metric("D值 (3)", f"{last_row['D']:.1f}")
            c4.metric("布林寬度", f"{(last_row['BB_Upper']-last_row['BB_Lower']):.2f}")

            # 3. AI 分析
            st.subheader("🤖 AI 策略分析報告")
            with st.spinner("AI 正在根據您的策略進行判斷..."):
                analysis_result = ask_gemini_analysis(symbol, df)
                st.markdown(f"""
                <div style="background-color:#f8f9fa; padding:20px; border-radius:10px; border-left: 5px solid #ff4b4b;">
                    {analysis_result.replace(chr(10), '<br>')}
                </div>
                """, unsafe_allow_html=True)
        else:
            st.error("查無此代號或數據獲取失敗，請確認代號正確。")
