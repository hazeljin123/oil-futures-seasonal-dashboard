# -*- coding: utf-8 -*-
"""
油脂油料期货季节性看板 构建脚本
- 数据源：新浪期货日线接口（HTFC 凭证为 undefined，按用户兜底改用新浪）
- 品种：豆粕(m) 豆油(y) 豆二(b) 棕榈(p) 菜油(OI) 菜粕(RM)
- 三界面(tab切换)：① 绝对价格(01/05/09) ② 月差 ③ 品种间价差
- 横轴：仅公历；按各届合约"实际交易区间"对齐（非强制 Jan-Dec），跨年连续
- 图例：每届合约单独可点选，命名采用合约代码(如 M2701)
- 无历届均值线；每日可由自动化重跑刷新
"""
import urllib.request, json, time, os, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
WS = HERE
OUT = os.path.join(HERE, "index.html")  # GitHub Pages 根目录入口文件名
OUT_CN = os.path.join(HERE, "油脂油料期货季节性看板.html")  # 中文名副本，与本地 F 盘文件同名
ECHARTS_PATH = os.path.join(HERE, "echarts.min.js")
CACHE_DIR = os.path.join(HERE, "cache")
CACHE = os.path.join(CACHE_DIR, "futures_raw.json")
REFRESH_FROM_YEAR = 2026   # 该年及以后(在市/近期)合约每日强制重抓，更早历史届用缓存，保证"每日更新"

SINA = "https://stock2.finance.sina.com.cn/futures/api/json.php/InnerFuturesNewService.getDailyKLine?symbol={sym}"

PRODUCTS = {
    "m":  {"name": "豆粕",   "exch": "DCE",  "unit": "元/吨"},
    "y":  {"name": "豆油",   "exch": "DCE",  "unit": "元/吨"},
    "b":  {"name": "豆二",   "exch": "DCE",  "unit": "元/吨"},
    "p":  {"name": "棕榈油", "exch": "DCE",  "unit": "元/吨"},
    "OI": {"name": "菜油",   "exch": "CZCE", "unit": "元/吨"},
    "RM": {"name": "菜粕",   "exch": "CZCE", "unit": "元/吨"},
}
PREFIX_DISP = {"m": "M", "y": "Y", "b": "B", "p": "P", "OI": "OI", "RM": "RM"}
ORDER = ["m", "y", "b", "p", "OI", "RM"]
YEARS = list(range(2019, 2028))          # 2019..2027
MONTHS = [f"{m:02d}" for m in range(1, 13)]
HIST_MAX = 2026                           # 历史届的截止年（排除 2027 部分数据）

SPREAD_PAIRS = {
    "m":  ["1-3", "3-5", "5-7", "7-9", "8-9", "9-11", "11-1", "12-1", "1-5", "5-9", "9-1"],
    "y":  ["1-3", "3-5", "5-7", "7-9", "8-9", "9-11", "11-1", "12-1", "1-5", "5-9", "9-1"],
    "RM": ["1-3", "3-5", "5-7", "7-9", "9-11", "11-1", "1-5", "5-9", "9-1"],
    "OI": ["1-3", "3-5", "5-7", "7-9", "9-11", "11-1", "1-5", "5-9", "9-1"],
    "p":  ["1-5", "5-9", "9-1", "2-5", "3-5", "4-5", "6-9", "7-9", "8-9", "10-1", "11-1", "12-1"],
    "b":  ["1-5", "5-9", "9-1", "2-5", "3-5", "4-5", "6-9", "7-9", "8-9", "10-1", "11-1", "12-1"],
}
INTER_GROUPS = [
    {"key": "m-RM",  "name": "豆粕 − 菜粕",           "months": [1, 3, 5, 7, 9, 11]},
    {"key": "y-OI",  "name": "豆油 − 菜油",           "months": [1, 3, 5, 7, 9, 11]},
    {"key": "OI-p",  "name": "菜油 − 棕榈油",         "months": [1, 3, 5, 7, 9, 11]},
    {"key": "y-p",   "name": "豆油 − 棕榈油",         "months": [1, 3, 5, 7, 8, 9, 11, 12]},
    {"key": "crush", "name": "内盘大豆榨利", "months": [1, 3, 5, 7, 8, 9, 11, 12], "note": "(78.5手豆粕+18.5手豆油-100手豆二)"},
]


def disp_code(prod, Y, mm):
    return f"{PREFIX_DISP[prod]}{Y%100:02d}{mm:02d}"


# ---------------- 抓取 ----------------
def fetch_raw(sym):
    url = SINA.format(sym=sym)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def load_cache():
    if os.path.exists(CACHE):
        try:
            with open(CACHE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cache(c):
    with open(CACHE, "w") as f:
        json.dump(c, f)


def build_contracts():
    c = load_cache()
    # 在市/近期年份每日强制重抓，确保"每日更新"；历史届复用缓存避免重复请求
    for prod in PRODUCTS:
        for y in YEARS:
            if y >= REFRESH_FROM_YEAR:
                c.setdefault(prod, {}).setdefault(str(y), {})
                for m in MONTHS:
                    c[prod][str(y)][m] = None
    for prod in PRODUCTS:
        c.setdefault(prod, {})
        for y in YEARS:
            c[prod].setdefault(str(y), {})
            for m in MONTHS:
                if not isinstance(c[prod][str(y)].get(m), dict):
                    c[prod][str(y)][m] = None
    need = []
    for prod in PRODUCTS:
        for y in YEARS:
            for m in MONTHS:
                if c[prod][str(y)][m] is None:
                    need.append((prod, y, m))
    print(f"[fetch] 待抓取合约数: {len(need)}")

    def work(item):
        prod, y, m = item
        sym = f"{prod}{str(y)[2:]}{m}"
        for _ in range(3):
            try:
                d = fetch_raw(sym)
                rec = {row["d"]: float(row["c"]) for row in d if row.get("c")}
                return (prod, y, m, rec if rec else None)
            except Exception:
                time.sleep(0.4)
        return (prod, y, m, None)

    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(work, it) for it in need]
        for f in as_completed(futs):
            prod, y, m, rec = f.result()
            c[prod][str(y)][m] = rec
            done += 1
            if done % 50 == 0:
                save_cache(c)
                print(f"[fetch] 进度 {done}/{len(need)}")
    save_cache(c)
    total = sum(1 for prod in PRODUCTS for y in YEARS for m in MONTHS if c[prod][str(y)][m])
    print(f"[fetch] 完成，有效合约 {total}")
    return c


def get_contract(c, prod, y, m):
    return c.get(prod, {}).get(str(y), {}).get(m)


def current_year(c, prod, m):
    ys = [y for y in YEARS if get_contract(c, prod, y, m)]
    return max(ys) if ys else None


def latest_data_date(c):
    """扫描全部合约的真实交易日，返回最新一根 K 线的日期（用于『数据截至』，避免与构建时间脱节）"""
    best = ""
    for prod in PRODUCTS:
        for y in YEARS:
            for m in MONTHS:
                rec = get_contract(c, prod, y, m)
                if isinstance(rec, dict):
                    for d in rec:
                        if isinstance(d, str) and d > best:
                            best = d
    return best or datetime.date.today().strftime("%Y-%m-%d")


# ---------------- 等待新浪放出当日日线 ----------------
# 新浪期货当日收盘的日线通常在收盘(15:00)后一段时间才挂出，15:32 直接抓会拿到前一天的数据。
# 因此：工作流仍按 15:32 启动，但脚本会轮询等待当日数据出现后再正式抓取，避免图表总是慢一天。
WAIT_MAX_MIN = 150      # 最多等待分钟数
WAIT_INTERVAL_SEC = 300 # 每 5 分钟探测一次


def expected_trading_date():
    """期望的最新交易日：工作日=今天；周末返回 None（不等待，本就没有新数据）"""
    today = datetime.date.today()
    if today.weekday() >= 5:   # 5=周六 6=周日
        return None
    return today.strftime("%Y-%m-%d")


def probe_sina_latest():
    """轻量探测：只抓几个活跃合约(当/明年 01 合约)，返回新浪当前已放出的最新交易日"""
    y0 = datetime.date.today().year
    best = ""
    for prod in ORDER:
        for y in (y0, y0 + 1):
            sym = f"{prod}{str(y)[2:]}01"
            try:
                rows = fetch_raw(sym)
                for row in rows:
                    d = row.get("d")
                    if isinstance(d, str) and d > best:
                        best = d
            except Exception:
                continue
    return best


def wait_for_today_data():
    """在正式抓取前，等待新浪放出当日日线；等到或超时即返回"""
    expected = expected_trading_date()
    if not expected:
        print("[wait] 周末/非交易日，无需等待当日数据")
        return
    waited = 0
    while waited < WAIT_MAX_MIN * 60:
        latest = probe_sina_latest()
        if latest >= expected:
            print(f"[wait] 新浪已放出 {latest} 日线（期望 {expected}），开始抓取")
            return
        print(f"[wait] 新浪当日({expected})日线尚未放出(当前最新 {latest or '未知'})，"
              f"{WAIT_INTERVAL_SEC // 60} 分钟后重试… 已等 {waited // 60} 分钟")
        time.sleep(WAIT_INTERVAL_SEC)
        waited += WAIT_INTERVAL_SEC
    print(f"[wait] 已等待 {WAIT_MAX_MIN} 分钟仍未放出 {expected}（可能节假日），按现有最新数据构建")


# ---------------- 计算 ----------------
# 每个点保留完整日期 "YYYY-MM-DD"，渲染时按各届合约实际生命周期平移对齐(见 JS dispTs)
def build_price(c):
    out = {}
    for prod in ORDER:
        out[prod] = {}
        for m in MONTHS:
            mm = int(m)
            cy = current_year(c, prod, m)
            cohorts = []
            for y in YEARS:
                if y > HIST_MAX or y == cy:
                    continue
                rec = get_contract(c, prod, y, m)
                if not rec:
                    continue
                dates, vals = [], []
                for d, cl in sorted(rec.items()):
                    dates.append(d); vals.append(round(cl, 1))
                cohorts.append({"year": y, "code": disp_code(prod, y, mm), "dates": dates, "vals": vals})
            cur = None
            if cy is not None:
                rec = get_contract(c, prod, cy, m)
                if rec:
                    dates, vals = [], []
                    for d, cl in sorted(rec.items()):
                        dates.append(d); vals.append(round(cl, 1))
                    cur = {"year": cy, "code": disp_code(prod, cy, mm), "dates": dates, "vals": vals}
            # 整个月份合约在所有年份都调不到任何收盘价 → 该合约不存在，不生成图表
            if cohorts or cur:
                out[prod][m] = {"cohorts": cohorts, "current": cur}
    return out


def build_spreads(c):
    out = {}
    for prod in ORDER:
        out[prod] = {}
        for p in SPREAD_PAIRS[prod]:
            mA, mB = (int(x) for x in p.split("-"))
            sa, sb = f"{mA:02d}", f"{mB:02d}"
            cyA = current_year(c, prod, sa)
            cyB = current_year(c, prod, sb)
            cohorts = []
            for Y in YEARS:
                if Y > HIST_MAX:
                    continue
                if cyA is not None and Y == cyA:
                    continue
                far_year = Y + 1 if mB <= mA else Y
                near = get_contract(c, prod, Y, sa)
                far = get_contract(c, prod, far_year, sb)
                if not near or not far:
                    continue
                dates, vals = [], []
                for d, cl in sorted(near.items()):
                    if d in far:
                        dates.append(d); vals.append(round(cl - far[d], 1))
                if dates:
                    code = f"{disp_code(prod, Y, mA)}-{disp_code(prod, far_year, mB)}"
                    cohorts.append({"year": Y, "code": code, "dates": dates, "vals": vals})
            cur = None
            if cyA is not None and cyB is not None:
                near = get_contract(c, prod, cyA, sa)
                far = get_contract(c, prod, cyB, sb)
                if far is None and mB <= mA:
                    far = get_contract(c, prod, cyA + 1, sb)
                if near and far:
                    dates, vals = [], []
                    for d, cl in sorted(near.items()):
                        if d in far:
                            dates.append(d); vals.append(round(cl - far[d], 1))
                    if dates:
                        cur = {"year": cyA, "code": f"{disp_code(prod, cyA, mA)}-{disp_code(prod, cyB, mB)}", "dates": dates, "vals": vals}
            out[prod][p] = {"cohorts": cohorts, "current": cur}
    return out


def prod_month_val(c, prod, Y, mm):
    return get_contract(c, prod, Y, f"{mm:02d}")


def inter_two(c, pA, YA, mm, pB, YB):
    a = prod_month_val(c, pA, YA, mm)
    b = prod_month_val(c, pB, YB, mm)
    if not a or not b:
        return None
    dates, vals = [], []
    for d, va in sorted(a.items()):
        if d in b:
            dates.append(d); vals.append(round(va - b[d], 1))
    return {"dates": dates, "vals": vals} if dates else None


def crush_val(c, Y, mm):
    m = prod_month_val(c, "m", Y, mm)
    y = prod_month_val(c, "y", Y, mm)
    b = prod_month_val(c, "b", Y, mm)
    if not (m and y and b):
        return None
    dates, vals = [], []
    for d, mv in sorted(m.items()):
        if d in y and d in b:
            v = 78.5 * mv + 18.5 * y[d] - 100 * b[d]
            dates.append(d); vals.append(round(v, 1))
    return {"dates": dates, "vals": vals} if dates else None


def build_inter(c):
    data = {}
    for g in INTER_GROUPS:
        key = g["key"]
        data[key] = {}
        for mm in g["months"]:
            cohorts = []
            cur = None
            if key == "crush":
                cyA = current_year(c, "m", f"{mm:02d}")
                for Y in YEARS:
                    if Y > HIST_MAX:
                        continue
                    if cyA is not None and Y == cyA:
                        continue
                    s = crush_val(c, Y, mm)
                    if not s:
                        continue
                    code = f"{disp_code('m', Y, mm)}·{disp_code('y', Y, mm)}·{disp_code('b', Y, mm)}"
                    cohorts.append({"year": Y, "code": code, "dates": s["dates"], "vals": s["vals"]})
                if cyA is not None:
                    cur = crush_val(c, cyA, mm)
                    if cur:
                        code = f"{disp_code('m', cyA, mm)}·{disp_code('y', cyA, mm)}·{disp_code('b', cyA, mm)}"
                        cur = {"year": cyA, "code": code, "dates": cur["dates"], "vals": cur["vals"]}
            else:
                pA, pB = key.split("-")
                cyA = current_year(c, pA, f"{mm:02d}")
                cyB = current_year(c, pB, f"{mm:02d}")
                for Y in YEARS:
                    if Y > HIST_MAX:
                        continue
                    if cyA is not None and Y == cyA:
                        continue
                    s = inter_two(c, pA, Y, mm, pB, Y)
                    if not s:
                        continue
                    code = f"{disp_code(pA, Y, mm)}-{disp_code(pB, Y, mm)}"
                    cohorts.append({"year": Y, "code": code, "dates": s["dates"], "vals": s["vals"]})
                if cyA is not None and cyB is not None:
                    cur = inter_two(c, pA, cyA, mm, pB, cyB)
                    if cur:
                        code = f"{disp_code(pA, cyA, mm)}-{disp_code(pB, cyB, mm)}"
                        cur = {"year": cyA, "code": code, "dates": cur["dates"], "vals": cur["vals"]}
            data[key][str(mm)] = {"cohorts": cohorts, "current": cur}
    return {"groups": INTER_GROUPS, "data": data}


# ---------------- HTML ----------------
def ensure_echarts():
    if not os.path.exists(ECHARTS_PATH):
        print("[echarts] 下载中…")
        url = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            with open(ECHARTS_PATH, "wb") as f:
                f.write(r.read())
        print("[echarts] 已缓存")
    with open(ECHARTS_PATH, "r", encoding="utf-8") as f:
        return f.read()


JS = r"""
const PRODS=DATA.products, ORDER=DATA.order;
const PRICE=DATA.price, SPREAD=DATA.spread, INTER=DATA.inter;
const YEAR_COLORS={2019:'#9aa5b1',2020:'#4e79a7',2021:'#59a14f',2022:'#f28e2b',2023:'#76b7b2',2024:'#af7aa1',2025:'#8d6e63',2026:'#e15759',2027:'#4995c9'};
const LIVE='#d81e2c';
const REF=2020;
const fmtN=v=>(v==null?'–':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:1}));
// 把某届合约某天的真实日期，平移到统一的参考时间轴(使各届同周期对齐，并保留实际交易区间/跨年连续)
function dispTs(fullDate, YY){
  const y=+fullDate.slice(0,4), mo=+fullDate.slice(5,7), da=+fullDate.slice(8,10);
  const dispY = y + (REF - (YY - 1));
  return new Date(dispY, mo-1, da).getTime();
}
function sdata(dates,vals,YY){ return dates.map((d,i)=>[dispTs(d,YY),vals[i]]); }
function tipFmt(ps){
  if(!ps||!ps.length) return '';
  const dt=new Date(ps[0].value[0]);
  let h='公历 <b>'+(dt.getMonth()+1)+'月'+dt.getDate()+'日</b> <span style="color:#8494a4;font-size:11px">（历届同周期对齐）</span>';
  let rows='<table style="border-collapse:collapse;font-size:12px">';
  ps.forEach(p=>{ const live=(p.color===LIVE);
    rows+='<tr><td style="padding:1px 8px;color:#8494a4">'+p.seriesName+'</td><td style="text-align:right;font-weight:'+(live?'700':'400')+';color:'+p.color+'">'+fmtN(p.value[1])+'</td></tr>';
  });
  return h+rows+'</table>';
}
function firstLast(obj){
  let firsts=[],lasts=[];
  const add=(dates,yy)=>{ if(!dates.length) return; firsts.push(dispTs(dates[0],yy)); lasts.push(dispTs(dates[dates.length-1],yy)); };
  (obj.cohorts||[]).forEach(c=>add(c.dates,c.year));
  if(obj.current) add(obj.current.dates,obj.current.year);
  firsts.sort((a,b)=>a-b); lasts.sort((a,b)=>a-b);
  const med=arr=>arr.length?arr[Math.floor(arr.length/2)]:null;
  return [med(firsts), med(lasts)];
}
function optOf(obj){
  const names=(obj.cohorts||[]).map(c=>c.code);
  if(obj.current) names.push(obj.current.code);
  const [amn,amx]=firstLast(obj);
  const pad=Math.max(1,(amx-amn)*0.02);
  const mn=amn-pad, mx=amx+pad;
  return {
    grid:{left:58,right:18,top:58,bottom:46},
    tooltip:{trigger:'axis',axisPointer:{type:'line',lineStyle:{color:'#aab'}},formatter:tipFmt,confine:true},
    legend:{type:'plain',top:6,left:6,right:6,itemWidth:14,itemHeight:8,itemGap:8,textStyle:{fontSize:10,color:'#6b7a89'},data:names},
    xAxis:{type:'time',min:mn,max:mx,
      axisLabel:{formatter:v=>{const d=new Date(v);return (d.getMonth()+1)+'/'+d.getDate();},color:'#6b7a89'},
      axisLine:{lineStyle:{color:'#cdd6df'}},splitLine:{show:false}},
    yAxis:{type:'value',scale:true,
      axisLabel:{color:'#6b7a89'},splitLine:{lineStyle:{color:'#eef2f6'}}},
    series:[]
  };
}
function buildSeries(obj){
  const [amn,amx]=firstLast(obj);
  const s=[];
  (obj.cohorts||[]).forEach(c=>{
    const data=sdata(c.dates,c.vals,c.year).filter(p=>p[0]>=amn && p[0]<=amx);
    s.push({name:c.code,type:'line',showSymbol:false,data:data,
      lineStyle:{width:1.1,color:YEAR_COLORS[c.year]||'#9aa5b1',opacity:0.55},itemStyle:{color:YEAR_COLORS[c.year]||'#9aa5b1'},z:2});
  });
  if(obj.current&&obj.current.dates.length){
    const data=sdata(obj.current.dates,obj.current.vals,obj.current.year).filter(p=>p[0]>=amn && p[0]<=amx);
    s.push({name:obj.current.code,type:'line',showSymbol:false,data:data,
      lineStyle:{width:2.6,color:LIVE},itemStyle:{color:LIVE},z:8});
  }
  return s;
}
const MONTHS=DATA.months;
const CHARTS=[]; const INITED={}; const SUBS={};
function addCard(host,cid,title,unit,obj,note){
  const div=document.createElement('div'); div.className='card';
  const noteHtml=note?'<span class="note">'+note+'</span>':'';
  div.innerHTML='<div class="chhead"><div class="ch-t">'+title+'<span class="unit">'+unit+'</span>'+noteHtml+'</div></div><div class="chart" id="'+cid+'"></div>';
  host.appendChild(div);
  CHARTS.push({pane:host.dataset.pane, sub:host.dataset.sub, cid, unit, obj});
}
function ensureSub(pane,sub){
  if(INITED[pane+'/'+sub]) return;
  INITED[pane+'/'+sub]=true;
  CHARTS.filter(c=>c.pane===pane && c.sub===sub).forEach(c=>{
    const el=document.getElementById(c.cid); const ch=echarts.init(el);
    const opt=optOf(c.obj); opt.series=buildSeries(c.obj); ch.setOption(opt); ch._o=opt;
  });
}
function resizeSub(pane,sub){
  CHARTS.filter(c=>c.pane===pane && c.sub===sub).forEach(c=>{
    const el=document.getElementById(c.cid); if(el&&echarts.getInstanceByDom(el)) echarts.getInstanceByDom(el).resize();
  });
}
function renderPane(pane,subs,buildSub){
  const paneEl=document.getElementById(pane);
  SUBS[pane]=subs.map(s=>s.key);
  const bar=document.createElement('div'); bar.className='subtabs';
  subs.forEach((s,i)=>{
    const sp=document.createElement('div'); sp.className='subpane'; sp.id='sub-'+pane+'-'+s.key;
    sp.dataset.pane=pane; sp.dataset.sub=s.key; sp.hidden=(i!==0);
    paneEl.appendChild(sp);
    buildSub(s, sp);
    const b=document.createElement('div'); b.className='stab'+(i===0?' on':''); b.textContent=s.label;
    b.onclick=()=>{
      bar.querySelectorAll('.stab').forEach(x=>x.classList.remove('on')); b.classList.add('on');
      paneEl.querySelectorAll('.subpane').forEach(x=>x.hidden=true);
      sp.hidden=false; ensureSub(pane,s.key); resizeSub(pane,s.key);
    };
    bar.appendChild(b);
  });
  paneEl.insertBefore(bar, paneEl.firstChild);
  if(!paneEl.hidden){ ensureSub(pane, subs[0].key); resizeSub(pane, subs[0].key); }
}
function buildPriceSub(s,host){
  const prod=s.key, info=PRODS[prod];
  Object.keys(PRICE[prod]).sort().forEach(m=>addCard(host,'pr-'+prod+'-'+m, info.name+' '+m+'合约 收盘价','元/吨',PRICE[prod][m]));
}
function buildSpreadSub(s,host){
  const prod=s.key, info=PRODS[prod];
  DATA.spreadPairs[prod].forEach(p=>addCard(host,'sp-'+prod+'-'+p, info.name+' '+p.replace('-','−')+' 月差','元/吨',SPREAD[prod][p]));
}
function buildInterSub(s,host){
  const g=INTER.groups.find(x=>x.key===s.key);
  g.months.forEach(mm=>addCard(host,'it-'+g.key+'-'+mm, g.name+' · '+mm+'月','元/吨',INTER.data[g.key][String(mm)], g.note));
}
function renderPrice(){ renderPane('panePrice', ORDER.map(p=>({key:p,label:PRODS[p].name})), buildPriceSub); }
function renderSpread(){ renderPane('paneSpread', ORDER.map(p=>({key:p,label:PRODS[p].name})), buildSpreadSub); }
function renderInter(){ renderPane('paneInter', INTER.groups.map(g=>({key:g.key,label:g.name})), buildInterSub); }
const TABS=[{id:'price',name:'① 绝对价格季节性',pane:'panePrice'},{id:'spread',name:'② 月差季节性',pane:'paneSpread'},{id:'inter',name:'③ 品种间价差季节性',pane:'paneInter'}];
function setupTabs(){
  const box=document.getElementById('tabs');
  TABS.forEach((t,i)=>{
    const b=document.createElement('div'); b.className='tab'+(i===0?' on':''); b.textContent=t.name;
    b.onclick=()=>{
      document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on')); b.classList.add('on');
      TABS.forEach(tt=>document.getElementById(tt.pane).hidden=true);
      document.getElementById(t.pane).hidden=false;
      ensureSub(t.pane, SUBS[t.pane][0]); resizeSub(t.pane, SUBS[t.pane][0]);
    };
    box.appendChild(b);
  });
}
window.addEventListener('resize',()=>{ for(const p in SUBS){ const pe=document.getElementById(p); if(pe.hidden) continue; SUBS[p].forEach(s=>{ if(!document.getElementById('sub-'+p+'-'+s).hidden) resizeSub(p,s); }); } });
document.getElementById('hDate').textContent=DATA.updated;
document.getElementById('hGen').textContent=DATA.generated_at;
setupTabs(); renderPrice(); renderSpread(); renderInter();
"""


def build_html(c):
    price = build_price(c)
    spread = build_spreads(c)
    inter = build_inter(c)
    DATA = {
        "updated": latest_data_date(c),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "products": PRODUCTS,
        "order": ORDER,
        "months": MONTHS,
        "price": price,
        "spread": spread,
        "spreadPairs": SPREAD_PAIRS,
        "inter": inter,
    }
    echarts_js = ensure_echarts()
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>油脂油料期货价格季节性看板</title>
<style>
:root{--bg:#f4f6f8;--card:#fff;--ink:#1c2733;--muted:#6b7a89;--line:#e4e9ef;--accent:#14538c}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.55}
.wrap{max-width:1400px;margin:0 auto;padding:0 20px 48px}
header{background:linear-gradient(135deg,#0d2b45 0%,#14538c 78%);color:#fff;padding:24px 0 18px;margin-bottom:16px}
header .wrap{padding-bottom:0}
.h-title{font-size:23px;font-weight:700;letter-spacing:.5px}
.h-title small{font-weight:400;opacity:.75;font-size:13px;margin-left:12px}
.h-sub{margin-top:6px;font-size:13px;opacity:.85}
.h-sub b{color:#ffd166}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 4px;position:sticky;top:0;background:var(--bg);padding:10px 0;z-index:50}
.tab{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:7px 16px;cursor:pointer;font-size:13.5px;color:var(--muted);user-select:none;transition:all .15s;font-weight:500}
.tab:hover{border-color:#b9c6d3}
.tab.on{background:var(--accent);border-color:var(--accent);color:#fff;box-shadow:0 2px 8px rgba(20,83,140,.3)}
.pane{display:block}
.pane[hidden]{display:none}
.subtabs{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0 12px;position:sticky;top:50px;background:var(--bg);padding:6px 0;z-index:40}
.stab{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:5px 13px;cursor:pointer;font-size:12.5px;color:var(--muted);user-select:none;transition:all .15s}
.stab:hover{border-color:#b9c6d3}
.stab.on{background:#0d2b45;border-color:#0d2b45;color:#fff;font-weight:600}
.subpane{display:grid;grid-template-columns:repeat(auto-fit,minmax(540px,1fr));gap:16px}
.subpane[hidden]{display:none}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px 8px;box-shadow:0 1px 3px rgba(28,39,51,.05)}
.chhead{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.ch-t{font-size:15px;font-weight:700}
.ch-t .unit{font-weight:400;margin-left:10px;white-space:nowrap;font-size:12px;color:var(--muted)}
.ch-t .note{font-weight:400;margin-left:8px;white-space:nowrap;font-size:11px;color:#9aa5b1}
.chart{height:370px;width:100%}
@media (max-width:640px){.subpane{grid-template-columns:1fr}.chart{height:320px}}
</style>
</head>
<body>
<header><div class="wrap">
<div class="h-title">油脂油料期货价格季节性看板<small>豆粕 / 豆油 / 豆二 / 棕榈 / 菜油 / 菜粕</small></div>
<div class="h-sub">大连商品交易所 · 郑州商品交易所 各月合约 <b>日线收盘价</b>（元/吨）· 数据截至 <b id="hDate"></b> · 数据源：新浪期货 · 每日 <b>15:32</b> 自动更新 · 最后自动更新 <b id="hGen"></b> · 横轴为<b>公历</b>，按各届合约<b>实际交易区间</b>对齐</div>
</div></header>
<div class="wrap">
<div class="tabs" id="tabs"></div>
<div class="pane" id="panePrice"></div>
<div class="pane" id="paneSpread" hidden></div>
<div class="pane" id="paneInter" hidden></div>
</div>
<script>""" + echarts_js + "</script>\n<script>const DATA = " + json.dumps(DATA, ensure_ascii=False) + ";\n" + JS + "</script>\n</body></html>"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[html] 已生成: {OUT}  ({len(html)/1024/1024:.2f} MB)")
    # 同步写一份中文名副本（与本地文件同名，便于在仓库中辨认）
    with open(OUT_CN, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[html] 已生成副本: {OUT_CN}")


if __name__ == "__main__":
    t0 = time.time()
    wait_for_today_data()
    c = build_contracts()
    build_html(c)
    print(f"[done] 总耗时 {time.time()-t0:.1f}s")
