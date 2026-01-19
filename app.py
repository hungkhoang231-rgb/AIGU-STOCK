import React, { useState, useMemo, useEffect } from 'react';
import { 
  Search, TrendingUp, TrendingDown, DollarSign, Activity, 
  BarChart2, Info, RefreshCw, Wifi, WifiOff, Sparkles, 
  Bot, Target, Zap, Award, ArrowLeft, Layers, ChevronRight,
  AlertTriangle, Radio, ShieldAlert, ShieldCheck, TrendingUp as TrendingUpIcon,
  HelpCircle, Globe, CandlestickChart, LineChart
} from 'lucide-react';
import { ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, Cell, ReferenceLine } from 'recharts';

// Gemini API Key (由環境自動注入)
const apiKey = "";

// FinMind API Token (台股用)
const FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJkYXRlIjoiMjAyNi0wMS0xOSAxMzo0NjozMCIsInVzZXJfaWQiOiJHVS1TdG9jay1BSSIsImVtYWlsIjoibG92aW5nbWV0ZW9yQGdtYWlsLmNvbSIsImlwIjoiMzYuMjI1LjEzMy4xMjcifQ.JaORk2rKeWusBmshTOi59yapLuWsUOKwq2Yt9mrAvBk";

// -----------------------------------------------------------------------------
// 技術指標運算工具
// -----------------------------------------------------------------------------

const calculateSMA = (data, window) => {
  if (data.length < window) return null;
  const sum = data.slice(data.length - window).reduce((acc, val) => acc + val, 0);
  return sum / window;
};

const calculateStdDev = (data, window, mean) => {
  if (data.length < window) return null;
  const slice = data.slice(data.length - window);
  const squaredDiffs = slice.map(val => Math.pow(val - mean, 2));
  return Math.sqrt(squaredDiffs.reduce((acc, val) => acc + val, 0) / window);
};

const calculateRSI = (prices, period = 14) => {
  if (prices.length < period + 1) return 50;
  let gains = 0, losses = 0;
  for (let i = 1; i <= period; i++) {
    const diff = prices[i] - prices[i - 1];
    if (diff >= 0) gains += diff; else losses += Math.abs(diff);
  }
  let avgGain = gains / period;
  let avgLoss = losses / period;
  for (let i = period + 1; i < prices.length; i++) {
    const diff = prices[i] - prices[i - 1];
    avgGain = (avgGain * (period - 1) + (diff >= 0 ? diff : 0)) / period;
    avgLoss = (avgLoss * (period - 1) + (diff < 0 ? Math.abs(diff) : 0)) / period;
  }
  return avgLoss === 0 ? 100 : Math.round(100 - (100 / (1 + (avgGain / avgLoss))));
};

const calculateKD = (highs, lows, closes, period = 9) => {
  if (closes.length < period) return { k: 50, d: 50 };
  const rHigh = Math.max(...highs.slice(-period));
  const rLow = Math.min(...lows.slice(-period));
  const rsv = rHigh === rLow ? 50 : ((closes[closes.length - 1] - rLow) / (rHigh - rLow)) * 100;
  const k = (2/3) * 50 + (1/3) * rsv;
  const d = (2/3) * 50 + (1/3) * k;
  return { k: parseFloat(k.toFixed(1)), d: parseFloat(d.toFixed(1)) };
};

// 簡單線性回歸計算斜率，用於判斷背離
const calculateSlope = (data) => {
    if (data.length < 2) return 0;
    const n = data.length;
    let sumX = 0, sumY = 0, sumXY = 0, sumXX = 0;
    for (let i = 0; i < n; i++) {
        sumX += i;
        sumY += data[i];
        sumXY += i * data[i];
        sumXX += i * i;
    }
    const slope = (n * sumXY - sumX * sumY) / (n * sumXX - sumX * sumX);
    return slope;
};

// -----------------------------------------------------------------------------
// 資料抓取與 API 連線
// -----------------------------------------------------------------------------

const processStockData = (rawItems, id) => {
    if (rawItems.length < 30) return null;

    const processed = rawItems.map((d, i, arr) => {
      const prices = arr.slice(0, i + 1).map(x => x.close);
      let ma20 = null, upper = null, lower = null, bandwidth = null;
      if (i >= 19) {
        ma20 = calculateSMA(prices, 20);
        const std = calculateStdDev(prices, 20, ma20);
        upper = ma20 + 2 * std;
        lower = ma20 - 2 * std;
        bandwidth = (upper - lower) / ma20;
      }
      return {
        ...d,
        ma20, upper, lower, bandwidth,
        isUp: d.close >= d.open,
        // 為 K 線圖準備的 Range Bar 數據：[最低價, 最高價]
        // 這樣 Bar 的高度就會涵蓋整根影線，我們再透過 CustomShape 畫出實體
        candleRange: [d.low, d.high]
      };
    }).slice(-40); 

    const bandwidths = processed.map(p => p.bandwidth).filter(b => b !== null);
    const currentBW = bandwidths[bandwidths.length - 1];
    const minBW = Math.min(...bandwidths.slice(-20));
    const isSqueeze = currentBW <= minBW * 1.1; 

    const closes = rawItems.map(x => x.close);
    const rsi = calculateRSI(closes);
    const kd = calculateKD(rawItems.map(x => x.high), rawItems.map(x => x.low), closes);
    const latest = processed[processed.length - 1];
    const prev = processed[processed.length - 2];

    // --- 風險指數 ---
    let positionInBand = 0.5;
    if (latest.upper && latest.lower && latest.upper !== latest.lower) {
        positionInBand = (latest.close - latest.lower) / (latest.upper - latest.lower);
    }
    const rsiRisk = rsi / 100;
    const rawRisk = ((positionInBand + rsiRisk) / 2) * 100;
    const riskScore = Math.max(1, Math.min(99, Math.round(rawRisk)));

    // --- 背離偵測 (Divergence) ---
    // 取過去 10 天的數據來計算趨勢
    let divergence = "無明顯背離";
    let divType = "none"; // none, bull, bear

    // 簡易背離邏輯：
    if (latest.close > prev.close && rsi < 50) {
        divergence = "量價/指標背離 (漲勢虛弱)";
        divType = "bear";
    } else if (latest.close < prev.close && rsi > 50) {
        divergence = "量價/指標背離 (跌勢有撐)";
        divType = "bull";
    }

    return {
      id, data: processed, rsi, k: kd.k, d: kd.d,
      price: latest.close,
      change: (latest.close - prev.close).toFixed(2),
      changePercent: ((latest.close - prev.close) / prev.close * 100).toFixed(2),
      upper: latest.upper, lower: latest.lower, ma20: latest.ma20,
      volume: latest.volume,
      isSqueeze,
      bandwidth: (currentBW * 100).toFixed(2),
      riskScore,
      divergence,
      divType
    };
};

const fetchTWStock = async (id) => {
    const startDate = new Date();
    startDate.setDate(startDate.getDate() - 120); 
    const dateStr = startDate.toISOString().split('T')[0];
    const url = `https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id=${id}&start_date=${dateStr}&token=${FINMIND_TOKEN}`;
    
    const res = await fetch(url);
    const json = await res.json();
    const raw = json.data || [];
    
    const standardized = raw.map(d => ({
        date: d.date.split('-').slice(1).join('/'),
        open: d.open, high: d.max, low: d.min, close: d.close,
        volume: Math.floor(d.Trading_Volume / 1000)
    }));

    return processStockData(standardized, id);
};

const fetchUSStock = async (id) => {
    const url = `https://corsproxy.io/?https://query1.finance.yahoo.com/v8/finance/chart/${id}?interval=1d&range=6mo`;
    
    const res = await fetch(url);
    const json = await res.json();
    
    if (!json.chart || !json.chart.result || json.chart.result.length === 0) {
        throw new Error("Symbol not found");
    }

    const result = json.chart.result[0];
    const quote = result.indicators.quote[0];
    const timestamps = result.timestamp;

    const standardized = [];
    for (let i = 0; i < timestamps.length; i++) {
        if (quote.close[i] === null) continue;

        const dateObj = new Date(timestamps[i] * 1000);
        const dateStr = `${(dateObj.getMonth() + 1).toString().padStart(2, '0')}/${dateObj.getDate().toString().padStart(2, '0')}`;
        
        standardized.push({
            date: dateStr,
            open: quote.open[i],
            high: quote.high[i],
            low: quote.low[i],
            close: quote.close[i], 
            volume: quote.volume[i]
        });
    }

    return processStockData(standardized, id);
};

const fetchStockFullData = async (input) => {
  try {
    const isTW = /^[0-9]+$/.test(input);
    if (isTW) {
        return await fetchTWStock(input);
    } else {
        return await fetchUSStock(input.toUpperCase());
    }
  } catch (e) {
    console.error(e);
    return null;
  }
};

// -----------------------------------------------------------------------------
// 客製化 K 線形狀 (Range Bar 實作)
// -----------------------------------------------------------------------------
const CandleStickShape = (props) => {
    const { x, y, width, height, payload } = props;
    const { open, close, high, low } = payload;
    const isUp = close > open;
    const color = isUp ? '#EF4444' : '#10B981';

    // 這裡的 y 和 height 是由 <Bar dataKey="candleRange" /> 提供的
    // y 代表最高價 (High) 的像素位置
    // height 代表 (High - Low) 的總像素高度
    
    // 計算像素與價格的比例
    const totalRange = Math.max(high - low, 0.0001); // 避免除以零
    const pixelRatio = height / totalRange;

    // 計算實體 (Body) 的位置
    const bodyTop = Math.max(open, close);
    const bodyBottom = Math.min(open, close);
    
    // 實體距離最高價 (y) 的像素距離
    const offsetTop = (high - bodyTop) * pixelRatio;
    
    // 實體的高度
    const bodyHeight = Math.max(1, (bodyTop - bodyBottom) * pixelRatio);

    return (
        <g>
            {/* 影線 (Wick) - 貫穿整個 Range */}
            <line 
                x1={x + width / 2} 
                y1={y} 
                x2={x + width / 2} 
                y2={y + height} 
                stroke={color} 
                strokeWidth={1.5} 
            />
            {/* 實體 (Body) */}
            <rect 
                x={x} 
                y={y + offsetTop} 
                width={width} 
                height={bodyHeight} 
                fill={color} 
                stroke={color} // 加 stroke 避免只有1px時看不見
            />
        </g>
    );
};

// -----------------------------------------------------------------------------
// 主程式組件
// -----------------------------------------------------------------------------

export default function StockAILab() {
  const [mode, setMode] = useState('home'); 
  const [inputCode, setInputCode] = useState('');
  const [stock, setStock] = useState(null);
  const [loading, setLoading] = useState(false);
  const [aiData, setAiData] = useState(null); 
  const [aiLoading, setAiLoading] = useState(false);
  
  // 新增圖表類型狀態: 'line' | 'candle'
  const [chartType, setChartType] = useState('line');

  const handleSearch = async (e) => {
    if (e) e.preventDefault();
    if (inputCode.length < 1) return;
    
    setLoading(true);
    setAiData(null); 
    const res = await fetchStockFullData(inputCode.trim());
    if (res) {
      setStock(res);
      setMode('detail');
      setInputCode('');
    } else {
      alert("無法取得資料，請檢查代號 (台股請輸入代碼，美股請輸入 Symbol)。");
    }
    setLoading(false);
  };

  const runAIAnalysis = async () => {
    setAiLoading(true);
    const squeezeStatus = stock.isSqueeze ? "【特別注意：目前正處於布林擠壓收口期，隨時可能大變盤】" : "波動率正常。";
    const divStatus = stock.divType !== 'none' ? `【背離警示：${stock.divergence}】` : "無明顯背離。";
    const marketType = /^[0-9]+$/.test(stock.id) ? "台股" : "美股";

    const prompt = `
      請扮演資深操盤手，分析${marketType} ${stock.id}。
      數據：現價 ${stock.price} (漲跌 ${stock.changePercent}%), RSI ${stock.rsi}, K ${stock.k}, D ${stock.d}, 風險指數 ${stock.riskScore}/100。
      技術狀態：${squeezeStatus} ${divStatus}
      布林軌道：上${stock.upper?.toFixed(2)}/中${stock.ma20?.toFixed(2)}/下${stock.lower?.toFixed(2)}。
      
      請直接回傳一段 **JSON 格式** 的字串 (不要有 markdown code block)，包含以下欄位：
      {
        "summary": "一句話總結目前的趨勢與背離狀況 (繁體中文)",
        "analysis": "詳細的技術分析建議 (繁體中文，150字以內)",
        "buy_price": "建議買入價格 (若不建議買入請填 '觀望')",
        "stop_loss": "建議停損價格 (若觀望請填 '-')",
        "trend": "看多" 或 "看空" 或 "盤整"
      }
    `;
    
    try {
      const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key=${apiKey}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] })
      });
      const data = await response.json();
      const rawText = data.candidates?.[0]?.content?.parts?.[0]?.text || "";
      const cleanJson = rawText.replace(/```json|```/g, '').trim();
      const parsed = JSON.parse(cleanJson);
      setAiData(parsed);

    } catch (e) {
      console.error(e);
      setAiData({
          summary: "AI 分析連線失敗，請稍後再試。",
          analysis: "無法取得詳細數據。",
          buy_price: "-",
          stop_loss: "-",
          trend: "未知"
      });
    }
    setAiLoading(false);
  };

  // Helper to determine risk color
  const getRiskColor = (score) => {
      if (score >= 75) return 'text-red-500';
      if (score >= 40) return 'text-yellow-500';
      return 'text-green-500';
  };
  
  const getRiskBg = (score) => {
      if (score >= 75) return 'bg-red-500';
      if (score >= 40) return 'bg-yellow-500';
      return 'bg-green-500';
  };

  if (mode === 'home') {
    return (
      <div className="min-h-screen bg-gray-900 flex flex-col items-center justify-center p-6">
        <div className="w-full max-w-md text-center space-y-8 animate-in fade-in zoom-in duration-500">
          <div className="inline-flex p-4 bg-gradient-to-br from-blue-600 to-indigo-700 rounded-3xl shadow-2xl shadow-blue-900/40">
            <Globe className="text-white" size={48} />
          </div>
          <div className="space-y-2">
            <h1 className="text-4xl font-black text-white tracking-tighter">STOCK AI PRO</h1>
            <p className="text-gray-400 text-sm">支援台股 (2330) 與 美股 (AAPL/NVDA)</p>
          </div>
          
          <form onSubmit={handleSearch} className="relative group">
            <div className="absolute -inset-1 bg-gradient-to-r from-blue-600 to-cyan-500 rounded-2xl blur opacity-25 group-hover:opacity-60 transition duration-300"></div>
            <div className="relative flex bg-gray-800 rounded-2xl p-2 border border-gray-700">
              <input 
                type="text" 
                placeholder="輸入代號 (如 2330 或 AAPL)" 
                className="w-full bg-transparent border-none text-white px-5 py-3 focus:outline-none text-xl font-mono uppercase"
                value={inputCode}
                onChange={(e) => setInputCode(e.target.value)}
              />
              <button 
                type="submit"
                className="bg-blue-600 hover:bg-blue-500 text-white px-6 rounded-xl font-bold transition-all flex items-center gap-2 active:scale-95"
                disabled={loading}
              >
                {loading ? <RefreshCw className="animate-spin" size={20} /> : <ChevronRight size={24} />}
              </button>
            </div>
          </form>

          <div className="grid grid-cols-2 gap-3">
            <div className="p-4 bg-gray-800/50 rounded-2xl border border-gray-700/50 text-left">
              <ShieldAlert className="text-red-500 mb-2" size={20} />
              <div className="text-[10px] text-gray-500 uppercase tracking-widest font-bold">Divergence</div>
              <div className="text-sm font-bold text-gray-200">背離偵測系統</div>
            </div>
            <div className="p-4 bg-gray-800/50 rounded-2xl border border-gray-700/50 text-left">
              <CandlestickChart className="text-cyan-500 mb-2" size={20} />
              <div className="text-[10px] text-gray-500 uppercase tracking-widest font-bold">K-Line</div>
              <div className="text-sm font-bold text-gray-200">日線 K 線圖</div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100 pb-12">
      <nav className="sticky top-0 z-[50] bg-gray-800/80 backdrop-blur-md border-b border-gray-700 px-4 py-2 flex items-center gap-3">
        <button onClick={() => setMode('home')} className="p-2 hover:bg-gray-700 rounded-full transition-colors shrink-0">
          <ArrowLeft size={20} />
        </button>
        
        <div className="flex flex-col shrink-0">
          <span className="font-mono text-lg font-bold leading-none">{stock.id}</span>
          {stock.isSqueeze && (
              <span className="text-[10px] text-amber-500 font-bold animate-pulse flex items-center gap-1">
                <AlertTriangle size={8} /> SQUEEZE
              </span>
          )}
        </div>

        <div className="flex-1"></div>

        <form onSubmit={handleSearch} className="relative flex items-center">
            <Search className="absolute left-3 text-gray-400" size={14} />
            <input 
              type="text" 
              placeholder="搜尋..." 
              className="w-24 sm:w-32 bg-gray-900/50 border border-gray-600 rounded-full pl-8 pr-3 py-1.5 text-sm focus:w-36 transition-all focus:outline-none focus:border-blue-500 focus:bg-gray-800 placeholder-gray-500 font-mono uppercase"
              value={inputCode}
              onChange={(e) => setInputCode(e.target.value)}
            />
        </form>
      </nav>

      <main className="max-w-6xl mx-auto p-4 space-y-6">
        
        {/* 指標卡片 */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          
          {/* 大卡片：風險指數 */}
          <div className="md:col-span-2 bg-gradient-to-br from-gray-800 to-gray-900 rounded-3xl p-6 border border-gray-700 shadow-xl relative group cursor-help transition-all hover:z-[60]">
            
            <div className="absolute inset-0 overflow-hidden rounded-3xl pointer-events-none">
                <div className="absolute top-0 right-0 p-4 opacity-10">
                    <Activity size={80} />
                </div>
            </div>

            <div className="absolute top-20 right-4 md:right-8 w-64 p-4 bg-gray-900/95 backdrop-blur-xl border border-gray-600 rounded-2xl shadow-[0_10px_40px_-10px_rgba(0,0,0,0.5)] opacity-0 group-hover:opacity-100 transition-all pointer-events-none z-[70] translate-y-2 group-hover:translate-y-0 duration-300">
                <div className="flex items-center gap-2 mb-3 pb-2 border-b border-gray-700">
                    <ShieldAlert size={18} className="text-blue-400"/>
                    <span className="text-sm font-bold text-white">背離與風險 (Divergence & Risk)</span>
                </div>
                <div className="space-y-2 text-xs text-gray-300 leading-relaxed">
                    <p>背離偵測：比對價格走勢與指標強度。</p>
                    <ul className="pl-2 space-y-1">
                        <li>🐻 <span className="text-rose-400 font-bold">頂背離</span>：價漲指標不漲，漲勢可能告終。</li>
                        <li>🐂 <span className="text-emerald-400 font-bold">底背離</span>：價跌指標不跌，跌勢可能趨緩。</li>
                    </ul>
                </div>
            </div>

            <div className="relative z-10">
                <div className="flex justify-between items-start">
                <span className="text-gray-400 text-xs font-bold uppercase tracking-widest border-b border-dashed border-gray-600 pb-0.5">即時報價</span>
                <span className={`text-xs px-2 py-1 rounded-lg font-bold ${Number(stock.change) >= 0 ? 'bg-red-500/20 text-red-500' : 'bg-green-500/20 text-green-500'}`}>
                    {Number(stock.change) >= 0 ? '▲ 上漲' : '▼ 下跌'}
                </span>
                </div>
                <div className="mt-4 flex items-baseline gap-3">
                <span className="text-6xl font-black tracking-tighter">{stock.price?.toFixed(2)}</span>
                <span className={`text-xl font-bold ${Number(stock.change) >= 0 ? 'text-red-500' : 'text-green-500'}`}>
                    {Number(stock.change) > 0 ? '+' : ''}{stock.changePercent}%
                </span>
                </div>
                <div className="mt-4 flex items-center justify-between gap-4">
                    <div className="flex items-center gap-2 flex-1">
                        <div className="flex-1 h-1.5 bg-gray-700 rounded-full overflow-hidden">
                            <div 
                                className={`h-full ${getRiskBg(stock.riskScore)} transition-all duration-1000`} 
                                style={{ width: `${stock.riskScore}%` }}
                            />
                        </div>
                        <span className={`text-xs font-bold ${getRiskColor(stock.riskScore)}`}>
                            Risk {stock.riskScore}
                        </span>
                    </div>
                    {stock.divergence && stock.divergence !== '無明顯背離' && (
                        <div className={`text-[10px] px-2 py-1 rounded border font-bold ${stock.divType === 'bear' ? 'border-rose-500 text-rose-400 bg-rose-500/10' : 'border-emerald-500 text-emerald-400 bg-emerald-500/10'}`}>
                            {stock.divergence}
                        </div>
                    )}
                </div>
            </div>
          </div>

          {/* 小卡片區 */}
          <div className="md:col-span-2 grid grid-cols-2 gap-3">
            {[
              { 
                lab: 'RSI (14)', 
                val: stock.rsi, 
                color: stock.rsi > 70 ? 'text-red-500' : stock.rsi < 30 ? 'text-green-500' : 'text-purple-400',
                desc: '相對強弱指標。>70 超買，<30 超賣。若股價創高但 RSI 未創高，為頂背離。'
              },
              { 
                lab: 'KD 指標', 
                val: `${stock.k} / ${stock.d}`, 
                color: 'text-orange-400',
                desc: '隨機指標。K值(快線)向上突破D值(慢線)為黃金交叉(偏多)。'
              },
              { 
                lab: '布林頻寬 %', 
                val: `${stock.bandwidth}%`, 
                color: stock.isSqueeze ? 'text-amber-500' : 'text-blue-400',
                desc: '布林帶寬度。數值越低代表波動壓縮極致，通常是大行情噴發的前兆。'
              },
              { 
                lab: '波動診斷', 
                val: stock.isSqueeze ? '⚡ 即將變盤' : '常態擴張', 
                color: stock.isSqueeze ? 'text-amber-500' : 'text-gray-400',
                desc: stock.isSqueeze ? '目前處於「擠壓期 (Squeeze)」，波動率極低，留意突破方向。' : '目前處於「擴張期」，趨勢延續中。'
              }
            ].map((item, i) => {
                const isLeft = i % 2 === 0;
                return (
                  <div key={i} className={`group relative cursor-help p-4 rounded-2xl border ${item.lab === '波動診斷' && stock.isSqueeze ? 'bg-amber-500/5 border-amber-500/30' : 'bg-gray-800/40 border-gray-700'} flex flex-col justify-center transition-all hover:bg-gray-800/60 hover:z-[60] hover:shadow-lg hover:border-gray-500`}>
                    
                    <div className={`absolute top-full mt-3 ${isLeft ? 'left-0 origin-top-left' : 'right-0 origin-top-right'} w-48 p-3 bg-gray-900/95 backdrop-blur-xl border border-gray-500 rounded-xl shadow-[0_10px_30px_rgba(0,0,0,0.5)] opacity-0 group-hover:opacity-100 transition-all pointer-events-none z-[70] translate-y-[-5px] group-hover:translate-y-0 duration-300`}>
                        <div className={`absolute top-[-6px] ${isLeft ? 'left-6' : 'right-6'} w-3 h-3 bg-gray-900 border-t border-l border-gray-500 rotate-45`}></div>
                        <p className="text-[11px] leading-relaxed text-gray-200 font-medium relative z-10">{item.desc}</p>
                    </div>

                    <span className="text-gray-500 text-[10px] uppercase font-black tracking-widest border-b border-dashed border-gray-600 pb-0.5 w-fit mb-1">{item.lab}</span>
                    <span className={`text-xl font-mono font-bold mt-1 ${item.color}`}>
                        {item.val}
                    </span>
                  </div>
                );
            })}
          </div>
        </div>

        {/* 圖表 */}
        <div className="bg-gray-800/80 rounded-3xl border border-gray-700 p-6 shadow-xl relative z-10">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-2 text-sm font-bold text-gray-300 uppercase tracking-widest">
              {chartType === 'line' ? <LineChart size={18} className="text-blue-500" /> : <CandlestickChart size={18} className="text-blue-500" />}
              {chartType === 'line' ? 'Trend Line' : 'Daily Candle'}
            </div>
            
            {/* 圖表切換按鈕 */}
            <div className="flex bg-gray-900 rounded-lg p-1 border border-gray-700">
                <button 
                    onClick={() => setChartType('line')}
                    className={`px-3 py-1 rounded-md text-[10px] font-bold transition-all ${chartType === 'line' ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-300'}`}
                >
                    線圖
                </button>
                <button 
                    onClick={() => setChartType('candle')}
                    className={`px-3 py-1 rounded-md text-[10px] font-bold transition-all ${chartType === 'candle' ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-300'}`}
                >
                    K 線
                </button>
            </div>
          </div>
          
          <div className="h-[350px] sm:h-[480px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              {chartType === 'line' ? (
                  // --- 線圖模式 ---
                  <ComposedChart data={stock.data}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} opacity={0.3} />
                    <XAxis dataKey="date" tick={{fontSize: 10, fill: '#6B7280'}} axisLine={false} tickLine={false} />
                    <YAxis domain={['auto', 'auto']} tick={{fontSize: 10, fill: '#6B7280'}} axisLine={false} tickLine={false} />
                    <Tooltip 
                      contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: '16px', boxShadow: '0 20px 25px -5px rgb(0 0 0 / 0.5)' }}
                      itemStyle={{ fontSize: '12px', fontWeight: 'bold' }}
                    />
                    <Area type="monotone" dataKey="upper" stroke="none" fill="#3B82F6" fillOpacity={0.03} />
                    <Area type="monotone" dataKey="lower" stroke="none" fill="#3B82F6" fillOpacity={0.03} />
                    <Line type="monotone" dataKey="upper" stroke="#3B82F6" strokeWidth={1} dot={false} strokeDasharray="4 4" opacity={0.5} />
                    <Line type="monotone" dataKey="lower" stroke="#3B82F6" strokeWidth={1} dot={false} strokeDasharray="4 4" opacity={0.5} />
                    <Line type="monotone" dataKey="ma20" stroke="#F59E0B" strokeWidth={2} dot={false} />
                    <Line type="monotone" dataKey="close" stroke="#FFFFFF" strokeWidth={3} dot={{ r: 0 }} activeDot={{ r: 6, fill: '#3B82F6', strokeWidth: 0 }} />
                  </ComposedChart>
              ) : (
                  // --- K線圖模式 ---
                  <ComposedChart data={stock.data}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} opacity={0.3} />
                    <XAxis dataKey="date" tick={{fontSize: 10, fill: '#6B7280'}} axisLine={false} tickLine={false} />
                    <YAxis domain={['auto', 'auto']} tick={{fontSize: 10, fill: '#6B7280'}} axisLine={false} tickLine={false} />
                    <Tooltip 
                      contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: '16px', boxShadow: '0 20px 25px -5px rgb(0 0 0 / 0.5)' }}
                      itemStyle={{ fontSize: '12px', fontWeight: 'bold' }}
                      // K線模式下 Tooltip 自定義
                      formatter={(value, name, props) => {
                          if (name === 'candleRange') return [null, null];
                          return [value, name];
                      }}
                    />
                    
                    {/* 布林通道 (保留但變淡) */}
                    <Line type="monotone" dataKey="upper" stroke="#3B82F6" strokeWidth={1} dot={false} strokeDasharray="4 4" opacity={0.3} />
                    <Line type="monotone" dataKey="lower" stroke="#3B82F6" strokeWidth={1} dot={false} strokeDasharray="4 4" opacity={0.3} />
                    <Line type="monotone" dataKey="ma20" stroke="#F59E0B" strokeWidth={1} dot={false} opacity={0.7} />

                    {/* 使用 Range Bar 繪製 K 線，涵蓋最低至最高價 */}
                    <Bar 
                        dataKey="candleRange" 
                        shape={<CandleStickShape />} 
                        isAnimationActive={false}
                    />
                  </ComposedChart>
              )}
            </ResponsiveContainer>
          </div>
        </div>

        {/* AI 策略中心 */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 relative z-10">
            
            <div className="lg:col-span-1 grid grid-cols-1 gap-4">
                <div className="bg-gray-800 rounded-3xl p-6 border border-gray-700 shadow-lg hover:z-20 relative">
                    <div className="flex items-center gap-3 mb-4">
                        <Target className="text-emerald-400" size={24} />
                        <h3 className="font-bold text-gray-200">建議買入</h3>
                    </div>
                    <div className="text-3xl font-black text-emerald-400 font-mono tracking-tighter">
                        {aiData ? aiData.buy_price : <span className="text-gray-600 text-xl">---</span>}
                    </div>
                    <p className="text-xs text-gray-500 mt-2">根據支撐位與趨勢運算</p>
                </div>

                <div className="bg-gray-800 rounded-3xl p-6 border border-gray-700 shadow-lg hover:z-20 relative">
                    <div className="flex items-center gap-3 mb-4">
                        <ShieldAlert className="text-rose-400" size={24} />
                        <h3 className="font-bold text-gray-200">建議停損</h3>
                    </div>
                    <div className="text-3xl font-black text-rose-400 font-mono tracking-tighter">
                        {aiData ? aiData.stop_loss : <span className="text-gray-600 text-xl">---</span>}
                    </div>
                    <p className="text-xs text-gray-500 mt-2">嚴格執行，保護本金</p>
                </div>
            </div>

            <div className="lg:col-span-2 bg-gradient-to-b from-gray-800 to-gray-900 rounded-3xl border border-gray-700 p-8 shadow-2xl relative overflow-hidden flex flex-col justify-between z-0">
                {aiLoading && (
                    <div className="absolute inset-0 bg-gray-900/60 backdrop-blur-[2px] z-10 flex items-center justify-center">
                        <div className="flex flex-col items-center gap-4">
                            <RefreshCw className="animate-spin text-blue-500" size={40} />
                            <span className="text-sm font-bold animate-pulse">正在運算最佳進場點...</span>
                        </div>
                    </div>
                )}
                
                <div>
                    <div className="flex items-center gap-4 mb-6">
                        <div className="p-3 bg-blue-600 rounded-2xl shadow-xl shadow-blue-900/40">
                            <Bot className="text-white" size={28} />
                        </div>
                        <div>
                            <h3 className="text-xl font-black">AI 策略分析師</h3>
                            <p className="text-xs text-gray-500 font-bold uppercase tracking-widest">Quantum Strategy Insight</p>
                        </div>
                    </div>

                    {aiData ? (
                        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4">
                            <div className="flex items-center gap-2">
                                <span className={`px-3 py-1 rounded-full text-xs font-bold ${aiData.trend === '看多' ? 'bg-red-500/20 text-red-400' : aiData.trend === '看空' ? 'bg-green-500/20 text-green-400' : 'bg-gray-700 text-gray-300'}`}>
                                    {aiData.trend || '趨勢分析'}
                                </span>
                                <span className="text-gray-300 font-bold">{aiData.summary}</span>
                            </div>
                            <div className="p-4 bg-gray-950/50 rounded-2xl border border-gray-700/30 text-gray-300 leading-relaxed text-sm">
                                {aiData.analysis}
                            </div>
                        </div>
                    ) : (
                        <div className="text-center py-8 text-gray-500 text-sm">
                            <p>點擊下方按鈕，獲取即時買賣策略與風險評估。</p>
                        </div>
                    )}
                </div>

                <button 
                    onClick={runAIAnalysis}
                    disabled={aiLoading}
                    className="mt-6 w-full py-4 bg-blue-600 hover:bg-blue-500 text-white rounded-2xl font-black transition-all flex items-center justify-center gap-3 shadow-lg shadow-blue-900/30 active:scale-95"
                >
                    <Sparkles size={22} />
                    開始分析
                </button>
            </div>
        </div>
      </main>
    </div>
  );
}
