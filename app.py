import streamlit as st
import yfinance as yf
import pandas_ta as ta
import google.generativeai as genai
from duckduckgo_search import DDGS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import time

# --- 頁面設定 ---
st.set_page_config(page_title="AI 美股超賣獵手", page_icon="📉", layout="wide")

# --- 側邊欄：設定與敏感資訊 ---
with st.sidebar:
    st.header("⚙️ 系統設定")
    
    # 嘗試從 Streamlit Secrets 讀取 Key，如果沒有則顯示輸入框
    if 'GEMINI_API_KEY' in st.secrets:
        GEMINI_API_KEY = st.secrets['GEMINI_API_KEY']
        st.success("API Key 已從系統安全載入")
    else:
        GEMINI_API_KEY = st.text_input("Gemini API Key", type="password")

    if 'GMAIL_USER' in st.secrets:
        GMAIL_USER = st.secrets['GMAIL_USER']
        GMAIL_PASSWORD = st.secrets['GMAIL_PASSWORD']
        st.success("Gmail 帳密已從系統安全載入")
    else:
        st.divider()
        st.info("若未設定 Secrets，請手動輸入：")
        GMAIL_USER = st.text_input("您的 Gmail 地址")
        GMAIL_PASSWORD = st.text_input("Gmail 應用程式密碼", type="password")
    
    TARGET_EMAIL = st.text_input("接收報告的 Email", value=GMAIL_USER)

# --- 主畫面 ---
st.title("📉 AI 美股超賣偵測與分析系統")
st.markdown("此系統利用 **Yahoo Finance** 公開數據掃描市場，並結合 **Gemini AI** 進行深度分析。")

DEFAULT_TICKERS = "AAPL, MSFT, GOOGL, AMZN, NVDA, TSLA, META, NFLX, AMD, INTC"
tickers_input = st.text_area("輸入要掃描的股票代碼 (用逗號分隔)", value=DEFAULT_TICKERS)

# --- 函式區 ---
def search_news(symbol):
    try:
        results = DDGS().text(f"{symbol} stock news financial outlook", max_results=3)
        if results:
            return "\n".join([f"- {r['title']}" for r in results])
        return "無相關新聞"
    except:
        return "無法取得即時新聞"

def ask_gemini(stock_info, news):
    if not GEMINI_API_KEY: return "請先設定 API Key"
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    分析目標：{stock_info['symbol']} (RSI: {stock_info['rsi']}, 現價: {stock_info['price']})
    新聞標題：{news}
    請用繁體中文，扮演分析師，150字內分析：
    1. 為何最近下跌？
    2. 現在適合買進嗎？
    3. 未來展望。
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI 分析失敗: {e}"

def send_email(html_content, recipient):
    if not GMAIL_USER or not GMAIL_PASSWORD: return False, "未設定 Gmail 帳密"
    msg = MIMEMultipart()
    msg['Subject'] = f'【AI 投資週報】{datetime.now().strftime("%Y-%m-%d")}'
    msg['From'] = GMAIL_USER
    msg['To'] = recipient
    msg.attach(MIMEText(html_content, 'html'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_PASSWORD)
            s.send_message(msg)
        return True, "成功"
    except Exception as e:
        return False, str(e)

# --- 執行按鈕 ---
if st.button("🚀 啟動分析", type="primary"):
    status_text = st.empty()
    bar = st.progress(0)
    ticker_list = [x.strip() for x in tickers_input.split(',')]
    oversold = []

    status_text.text("正在掃描數據...")
    for i, sym in enumerate(ticker_list):
        try:
            df = yf.download(sym, period="3mo", progress=False)
            if len(df) > 14:
                # 簡單處理 Series 數據格式問題
                close_val = df['Close'].iloc[-1]
                current_price = float(close_val.item()) if hasattr(close_val, 'item') else float(close_val)
                
                rsi_series = ta.rsi(df['Close'], length=14)
                if rsi_series is not None and not rsi_series.empty:
                    rsi_val = rsi_series.iloc[-1]
                    current_rsi = float(rsi_val.item()) if hasattr(rsi_val, 'item') else float(rsi_val)
                    
                    # 篩選條件 (RSI < 45)
                    if current_rsi < 45:  
                        oversold.append({'symbol': sym, 'price': round(current_price, 2), 'rsi': round(current_rsi, 2)})
        except Exception as e:
            print(f"跳過 {sym}: {e}")
        bar.progress((i+1)/len(ticker_list))

    if not oversold:
        st.warning("目前市場沒有符合超賣條件 (RSI < 45) 的股票。")
        st.stop()

    # 取前5名並分析
    oversold.sort(key=lambda x: x['rsi'])
    top_5 = oversold[:5]
    
    report_html = "<h2>AI 分析報告</h2><hr>"
    for stock in top_5:
        with st.spinner(f"正在分析 {stock['symbol']} ..."):
            news = search_news(stock['symbol'])
            analysis = ask_gemini(stock, news)
            
            # 顯示在網頁
            with st.expander(f"📊 {stock['symbol']} (RSI: {stock['rsi']})", expanded=True):
                st.markdown(f"**現價:** ${stock['price']}")
                st.info(analysis)
            
            # 寫入 Email HTML
            report_html += f"""
            <div style="margin-bottom:15px; border-bottom:1px solid #ccc; padding-bottom:10px;">
                <h3 style="color:#2e86c1;">{stock['symbol']} (RSI: {stock['rsi']})</h3>
                <p><b>現價:</b> ${stock['price']}</p>
                <p>{analysis.replace(chr(10), '<br>')}</p>
            </div>
            """

    if GMAIL_USER:
        status_text.text("正在寄送郵件...")
        ok, msg = send_email(report_html, TARGET_EMAIL)
        if ok: 
            st.success(f"✅ 報告已寄出至 {TARGET_EMAIL}")
            st.balloons()
        else: 
            st.error(f"❌ 寄信失敗: {msg}")