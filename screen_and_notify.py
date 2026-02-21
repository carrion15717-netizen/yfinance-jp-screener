import pandas as pd
import yfinance as yf
import numpy as np
import os
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ====================== 設定 ======================
CSV_PATH = 'stock_list/Export/japanese_stocks_data_latest.csv'  # あなたの最新CSVパス（Actionsで生成されるものに合わせて調整）
GMAIL_USER = os.getenv('GMAIL_USER')
GMAIL_APP_PASS = os.getenv('GMAIL_APP_PASS')

# ====================== 計算関数 ======================
def calc_volume_ratio(vol, short=6, long=25):
    return vol[-short:].mean() / vol[-long:].mean() if len(vol) >= long else 0

def calc_ma_deviation(close, period=25):
    ma = close.rolling(period).mean().iloc[-1]
    return (close.iloc[-1] - ma) / ma * 100 if ma != 0 else 0

def calc_5y_decline(close, hist5y):
    if len(hist5y) < 200: return 0
    price_5y_ago = hist5y['Close'].iloc[0]
    return (close.iloc[-1] / price_5y_ago - 1) * 100

def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs)).iloc[-1]

def calc_macd_buy(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()
    return macd.iloc[-1] > sig.iloc[-1]

def is_golden_cross(close):
    ma5 = close.rolling(5).mean().iloc[-1]
    ma25 = close.rolling(25).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else 0
    ma75 = close.rolling(75).mean().iloc[-1] if len(close) >= 75 else 0
    ma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else 0
    gc2 = ma25 > ma75 if ma75 != 0 else False
    gc3 = ma50 > ma200 if ma200 != 0 else False
    return gc2 or gc3

# ====================== メイン処理 ======================
df = pd.read_csv(CSV_PATH)
# 基本フィルタ（高速化のため）
df = df[df['symbol'].str.endswith('.T')].copy()
print(f"全日本株: {len(df)}銘柄")

results = {"逆張り": [], "順張り": [], "低位株バズ": []}

for _, row in df.iterrows():
    ticker = row['symbol']
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        market_cap = info.get('marketCap') or 0
        if market_cap < 50_000_000_000:
            continue

        price = info.get('regularMarketPrice') or info.get('currentPrice') or 0
        if price == 0:
            continue

        # 直近40日履歴（MA・出来高用）
        hist = stock.history(period="40d")
        if len(hist) < 25:
            continue
        close = hist['Close']
        vol = hist['Volume']

        vol_ratio = calc_volume_ratio(vol)
        prev_change = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100
        ma_dev25 = calc_ma_deviation(close, 25)
        ma_dev5 = calc_ma_deviation(close, 5)
        rsi = calc_rsi(close)
        macd_buy = calc_macd_buy(close)
        golden = is_golden_cross(close)

        # 5年下落率（条件①のみ）
        decline_5y = 0
        if '逆張り' in results:  # 常に計算してもOK
            hist5y = stock.history(period="5y")
            decline_5y = calc_5y_decline(close, hist5y)

        growth_forecast = info.get('earningsGrowth', 0) or 0

        # ============== 条件判定 ==============
        # ① 逆張り
        if (decline_5y <= -50 and
            vol_ratio >= 2.0 and
            prev_change > 0 and
            ma_dev25 > 0):
            results["逆張り"].append({
                "コード": ticker.replace('.T',''), "銘柄": info.get('longName',''),
                "時価総額(億円)": round(market_cap/1e8,1), "前日騰落": round(prev_change,1),
                "5年下落": round(decline_5y,1), "出来高倍率": round(vol_ratio,2)
            })

        # ② 順張り
        if (growth_forecast >= 0.10 and
            golden and
            macd_buy and
            50 < rsi < 80 and
            vol_ratio >= 1.5):
            results["順張り"].append({
                "コード": ticker.replace('.T',''), "銘柄": info.get('longName',''),
                "成長率(予)": round(growth_forecast*100,1), "RSI": round(rsi,1),
                "出来高倍率": round(vol_ratio,2), "ゴールデンクロス": "該当"
            })

        # ③ 低位株バズ
        if (100 <= price <= 1000 and
            vol_ratio >= 3.0 and
            prev_change >= 5 and
            ma_dev5 > 0):
            results["低位株バズ"].append({
                "コード": ticker.replace('.T',''), "銘柄": info.get('longName',''),
                "株価": round(price,0), "前日騰落": round(prev_change,1),
                "出来高倍率": round(vol_ratio,2)
            })

    except Exception as e:
        continue  # エラー銘柄はスキップ

# ====================== メール送信 ======================
if any(len(v) > 0 for v in results.values()):
    html = f"<h2>【毎日スクリーニング】{datetime.now().strftime('%Y-%m-%d %H:%M')} 該当あり</h2>"
    for cat, lst in results.items():
        if lst:
            html += f"<h3>■ {cat} ({len(lst)}銘柄)</h3>"
            html += pd.DataFrame(lst).to_html(index=False, escape=False)

    msg = MIMEMultipart()
    msg['Subject'] = f'【株スクリーニング】該当あり - {datetime.now().strftime("%m/%d")}'
    msg['From'] = GMAIL_USER
    msg['To'] = GMAIL_USER
    msg.attach(MIMEText(html, 'html'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.send_message(msg)
    print("✅ 該当あり → メール送信完了")
else:
    print("該当なし")
