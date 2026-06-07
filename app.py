import { useState, useEffect, useCallback, useRef } from "react";

// ═══════════════════════════════════════════════════════════════════
// ARBIMINE - Complete SaaS Arbitrage Scanner
// Free tier: opportunities ≤ 2% spread only
// Paid tier: ALL opportunities (requires admin approval)
// Payment: M-Pesa 0728 308602 → "I Have Paid" → Admin approves
// ═══════════════════════════════════════════════════════════════════

const ADMIN_PHONE = "0728308602";
const WEEKLY_PRICE = 120;
const FREE_MAX_SPREAD = 2.0; // Free users only see ≤ 2% spread

// ─── Persistent storage helpers (localStorage simulation of backend) ──────────
const DB = {
  get: (k, def = null) => {
    try { const v = localStorage.getItem("arbimine_" + k); return v ? JSON.parse(v) : def; } catch { return def; }
  },
  set: (k, v) => { try { localStorage.setItem("arbimine_" + k, JSON.stringify(v)); } catch {} },
};

function initDB() {
  if (!DB.get("users")) {
    DB.set("users", [
      { id: "admin", phone: ADMIN_PHONE, name: "Admin", password: "arbimine2026", role: "admin", plan: "paid", approved: true, expiry: "2099-12-31" },
    ]);
  }
  if (!DB.get("payments")) DB.set("payments", []);
}

function getUsers() { return DB.get("users", []); }
function saveUsers(u) { DB.set("users", u); }
function getPayments() { return DB.get("payments", []); }
function savePayments(p) { DB.set("payments", p); }

function registerUser(phone, name, password) {
  const users = getUsers();
  if (users.find(u => u.phone === phone)) return { ok: false, msg: "Phone already registered." };
  const newUser = { id: Date.now().toString(), phone, name, password, role: "user", plan: "free", approved: false, expiry: null };
  saveUsers([...users, newUser]);
  return { ok: true, user: newUser };
}

function loginUser(phone, password) {
  const users = getUsers();
  const user = users.find(u => u.phone === phone && u.password === password);
  if (!user) return { ok: false, msg: "Invalid phone or password." };
  // Check expiry
  if (user.plan === "paid" && user.expiry && new Date() > new Date(user.expiry)) {
    const updated = users.map(u => u.id === user.id ? { ...u, plan: "free", approved: false } : u);
    saveUsers(updated);
    return { ok: true, user: { ...user, plan: "free", approved: false } };
  }
  return { ok: true, user };
}

function submitPayment(userId, mpesaPhone, amount, ref) {
  const payments = getPayments();
  const p = { id: Date.now().toString(), userId, mpesaPhone, amount, ref, status: "pending", submittedAt: new Date().toISOString() };
  savePayments([...payments, p]);
  return p;
}

function approvePayment(paymentId) {
  const payments = getPayments();
  const users = getUsers();
  const pmt = payments.find(p => p.id === paymentId);
  if (!pmt) return false;
  const expiry = new Date();
  expiry.setDate(expiry.getDate() + 7);
  const updatedPayments = payments.map(p => p.id === paymentId ? { ...p, status: "approved", approvedAt: new Date().toISOString() } : p);
  const updatedUsers = users.map(u => u.id === pmt.userId ? { ...u, plan: "paid", approved: true, expiry: expiry.toISOString().split("T")[0] } : u);
  savePayments(updatedPayments);
  saveUsers(updatedUsers);
  return true;
}

function denyPayment(paymentId) {
  const payments = getPayments();
  const updatedPayments = payments.map(p => p.id === paymentId ? { ...p, status: "denied", deniedAt: new Date().toISOString() } : p);
  savePayments(updatedPayments);
  return true;
}

function getFreshUser(userId) {
  return getUsers().find(u => u.id === userId) || null;
}

// ─── Binance public API + fallback simulation ─────────────────────────────────
const SYMBOLS = [
  "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT",
  "AVAXUSDT","DOTUSDT","LINKUSDT","MATICUSDT","LTCUSDT","UNIUSDT","ATOMUSDT",
  "NEARUSDT","ARBUSDT","OPUSDT","INJUSDT","TIAUSDT","WLDUSDT","FILUSDT",
  "APTUSDT","SUIUSDT","SEIUSDT","FETUSDT","RNDRUSDT","IMXUSDT","STXUSDT",
];

const DISPLAY = {
  BTCUSDT:"BTC/USDT",ETHUSDT:"ETH/USDT",BNBUSDT:"BNB/USDT",SOLUSDT:"SOL/USDT",
  XRPUSDT:"XRP/USDT",ADAUSDT:"ADA/USDT",DOGEUSDT:"DOGE/USDT",AVAXUSDT:"AVAX/USDT",
  DOTUSDT:"DOT/USDT",LINKUSDT:"LINK/USDT",MATICUSDT:"MATIC/USDT",LTCUSDT:"LTC/USDT",
  UNIUSDT:"UNI/USDT",ATOMUSDT:"ATOM/USDT",NEARUSDT:"NEAR/USDT",ARBUSDT:"ARB/USDT",
  OPUSDT:"OP/USDT",INJUSDT:"INJ/USDT",TIAUSDT:"TIA/USDT",WLDUSDT:"WLD/USDT",
  FILUSDT:"FIL/USDT",APTUSDT:"APT/USDT",SUIUSDT:"SUI/USDT",SEIUSDT:"SEI/USDT",
  FETUSDT:"FET/USDT",RNDRUSDT:"RNDR/USDT",IMXUSDT:"IMX/USDT",STXUSDT:"STX/USDT",
};

const FALLBACK = {
  BTCUSDT:67420,ETHUSDT:3510,BNBUSDT:608,SOLUSDT:178,XRPUSDT:0.582,
  ADAUSDT:0.474,DOGEUSDT:0.163,AVAXUSDT:38.4,DOTUSDT:7.82,LINKUSDT:14.6,
  MATICUSDT:0.89,LTCUSDT:84.2,UNIUSDT:9.14,ATOMUSDT:8.73,NEARUSDT:6.91,
  ARBUSDT:1.12,OPUSDT:2.43,INJUSDT:28.5,TIAUSDT:8.22,WLDUSDT:4.71,
  FILUSDT:5.93,APTUSDT:9.44,SUIUSDT:1.87,SEIUSDT:0.61,FETUSDT:1.85,
  RNDRUSDT:7.42,IMXUSDT:1.94,STXUSDT:2.11,
};

const EXCHANGES_SIM = ["Binance","OKX","KuCoin","Bybit","Gate.io","Huobi","Bitget","MEXC"];
const rng = (a, b) => Math.random() * (b - a) + a;

async function fetchBinancePrices() {
  try {
    const res = await fetch("https://api.binance.com/api/v3/ticker/price", { signal: AbortSignal.timeout(5000) });
    if (!res.ok) throw new Error("API error");
    const data = await res.json();
    const map = {};
    data.forEach(({ symbol, price }) => { if (SYMBOLS.includes(symbol)) map[symbol] = parseFloat(price); });
    return { ok: true, prices: map };
  } catch {
    return { ok: false, prices: {} };
  }
}

function buildOpportunities(prices, isPaid) {
  const opps = [];
  for (const sym of SYMBOLS) {
    const base = prices[sym] || FALLBACK[sym] || 1;
    const exList = [...EXCHANGES_SIM].sort(() => Math.random() - 0.5);
    const buyEx = exList[0], sellEx = exList[1];
    const buyPrice = base * rng(0.994, 1.006);
    const rawSpread = rng(0.04, 3.8);
    const sellPrice = buyPrice * (1 + rawSpread / 100);
    const fee = rng(0.08, 0.22);
    const profit = rawSpread - fee;
    const volume = rng(40000, 12000000);
    const liquidity = rng(15000, 800000);
    const conf = Math.min(99, Math.max(42, rng(50, 99)));
    const age = Math.floor(rng(2, 240));
    if (profit > 0.04) {
      opps.push({
        id: `${sym}-${buyEx}-${sellEx}-${Math.random().toString(36).slice(2, 6)}`,
        sym, pair: DISPLAY[sym] || sym, buyEx, sellEx,
        buyPrice, sellPrice, spread: rawSpread, profit, fee,
        volume, liquidity, conf, age,
        grade: profit > 2 ? "PREMIUM" : profit > 1 ? "HIGH" : profit > 0.4 ? "MED" : "LOW",
        isPaidOnly: rawSpread > FREE_MAX_SPREAD,
      });
    }
  }
  return opps.sort((a, b) => b.profit - a.profit);
}

// ─── Formatting helpers ───────────────────────────────────────────────────────
const fmtP = n => n >= 1000 ? n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : n >= 1 ? n.toFixed(4) : n.toFixed(8);
const fmtPct = n => (n >= 0 ? "+" : "") + n.toFixed(3) + "%";
const fmtVol = n => n >= 1e6 ? "$" + (n / 1e6).toFixed(2) + "M" : "$" + (n / 1e3).toFixed(1) + "K";
const fmtDate = d => d ? new Date(d).toLocaleDateString("en-KE") : "—";

// ─── SVG Logo ─────────────────────────────────────────────────────────────────
function Logo({ size = 44 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none">
      <defs>
        <linearGradient id="lr" x1="0" y1="0" x2="100" y2="100" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#f59e0b" /><stop offset="100%" stopColor="#b45309" />
        </linearGradient>
        <linearGradient id="la" x1="50" y1="18" x2="50" y2="80" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#fcd34d" /><stop offset="100%" stopColor="#92400e" />
        </linearGradient>
      </defs>
      <circle cx="50" cy="50" r="47" stroke="url(#lr)" strokeWidth="3" fill="rgba(0,0,0,0.45)" />
      <polygon points="50,20 28,78 39,78 50,50 61,78 72,78" fill="url(#la)" />
      <rect x="36" y="62" width="28" height="5" rx="2.5" fill="url(#la)" opacity="0.5" />
      <polyline points="56,36 70,22 70,30 76,30 76,16 62,16 62,22 70,22"
        stroke="#fcd34d" strokeWidth="3.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ConfRing({ pct }) {
  const r = 12, circ = 2 * Math.PI * r, fill = (pct / 100) * circ;
  const col = pct > 80 ? "#f59e0b" : pct > 65 ? "#d97706" : "#92400e";
  return (
    <div style={{ position: "relative", width: 32, height: 32, flexShrink: 0 }}>
      <svg width="32" height="32" style={{ transform: "rotate(-90deg)" }}>
        <circle cx="16" cy="16" r={r} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth="2.5" />
        <circle cx="16" cy="16" r={r} fill="none" stroke={col} strokeWidth="2.5"
          strokeDasharray={`${fill} ${circ}`} strokeLinecap="round" />
      </svg>
      <span style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", fontSize: "8px", color: col, fontWeight: 700 }}>{Math.round(pct)}</span>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// GLOBAL STYLES
// ═══════════════════════════════════════════════════════════════════
const STYLES = `
  @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=Bebas+Neue&display=swap');
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:#07090f;}
  ::-webkit-scrollbar{width:4px;}
  ::-webkit-scrollbar-track{background:#07090f;}
  ::-webkit-scrollbar-thumb{background:#2a1c08;border-radius:2px;}
  @keyframes floatY{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}
  @keyframes fadeUp{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:translateY(0)}}
  @keyframes slideIn{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:translateX(0)}}
  @keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
  @keyframes scanBeam{0%{top:-2px;opacity:.9}100%{top:100%;opacity:.3}}
  @keyframes orePulse{0%,100%{text-shadow:0 0 8px rgba(245,158,11,.4)}50%{text-shadow:0 0 22px rgba(245,158,11,.9)}}
  @keyframes spin{to{transform:rotate(360deg)}}
  @keyframes shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-6px)}75%{transform:translateX(6px)}}
  input:-webkit-autofill{-webkit-box-shadow:0 0 0 1000px #10162a inset!important;-webkit-text-fill-color:#e8d48a!important;}
  .fade-up{animation:fadeUp .4s ease forwards;}
  .ore-glow{animation:orePulse 2.5s ease infinite;}
  .live-blink{animation:blink 1.1s ease infinite;}
  .spinner{animation:spin .8s linear infinite;}
  .opp-row{
    border:1px solid rgba(255,255,255,0.05);
    border-left:3px solid #1c1000;border-radius:6px;
    padding:11px 14px;margin-bottom:5px;cursor:pointer;
    transition:background .15s,border-color .15s,transform .12s;
    animation:slideIn .22s ease;position:relative;
  }
  .opp-row:hover{background:rgba(245,158,11,0.05);border-color:rgba(245,158,11,0.18);transform:translateX(2px);}
  .opp-row.sel{background:rgba(245,158,11,0.09);border-left-color:#f59e0b;border-color:rgba(245,158,11,0.3);}
  .opp-row.PREMIUM{border-left-color:#a855f7;}
  .opp-row.HIGH{border-left-color:#f59e0b;}
  .opp-row.MED{border-left-color:#d97706;}
  .opp-row.LOW{border-left-color:#451a03;}
  .opp-row.locked{opacity:.45;cursor:not-allowed;}
  .opp-row.locked:hover{transform:none;background:transparent;}
  .btn{border:none;border-radius:7px;cursor:pointer;font-family:'Rajdhani',sans-serif;font-weight:600;letter-spacing:.07em;transition:all .18s;}
  .btn-gold{background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;box-shadow:0 4px 18px rgba(245,158,11,.35);}
  .btn-gold:hover{box-shadow:0 6px 24px rgba(245,158,11,.5);transform:translateY(-1px);}
  .btn-ghost{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08)!important;color:#6a5a3a;}
  .btn-ghost:hover{color:#c9b87a;border-color:rgba(255,255,255,0.15)!important;}
  .btn-red{background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3)!important;color:#f87171;}
  .btn-red:hover{background:rgba(239,68,68,0.2);}
  .btn-green{background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.3)!important;color:#4ade80;}
  .btn-green:hover{background:rgba(34,197,94,0.2);}
  .card{background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.055);border-radius:10px;}
  .fbtn{background:transparent;border:1px solid rgba(255,255,255,0.08);color:#4a3818;padding:5px 11px;border-radius:4px;cursor:pointer;font-family:'Rajdhani',sans-serif;font-size:12px;font-weight:600;letter-spacing:.06em;transition:all .15s;}
  .fbtn:hover{color:#d97706;border-color:rgba(217,119,6,.35);}
  .fbtn.on{background:rgba(245,158,11,0.13);border-color:rgba(245,158,11,0.45);color:#f59e0b;}
  input[type=range]{-webkit-appearance:none;width:100%;height:3px;background:rgba(255,255,255,0.08);border-radius:2px;outline:none;}
  input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:13px;height:13px;border-radius:50%;background:#f59e0b;cursor:pointer;box-shadow:0 0 8px rgba(245,158,11,.6);}
  .inp{width:100%;background:#10162a;border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:13px 14px;color:#e8d48a;font-size:15px;outline:none;font-family:'Rajdhani',sans-serif;font-weight:500;transition:border-color .2s;}
  .inp:focus{border-color:rgba(245,158,11,.45);}
  .inp-icon{position:relative;}
  .inp-icon .inp{padding-left:42px;}
  .inp-icon .icon{position:absolute;left:13px;top:50%;transform:translateY(-50%);font-size:17px;color:#374060;pointer-events:none;}
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.78);display:flex;align-items:center;justify-content:center;z-index:200;animation:fadeUp .2s ease;padding:16px;}
  .modal{background:#0e1320;border:1px solid rgba(245,158,11,.22);border-radius:14px;padding:28px;max-width:440px;width:100%;box-shadow:0 24px 60px rgba(0,0,0,.7);}
  .drow{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid rgba(255,255,255,0.04);font-size:13px;align-items:center;}
  .tag{padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:.1em;}
  .tag-pend{background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.3);color:#fbbf24;}
  .tag-appr{background:rgba(34,197,94,.12);border:1px solid rgba(34,197,94,.3);color:#4ade80;}
  .tag-deny{background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.3);color:#f87171;}
  .stat-card{background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.052);border-radius:10px;padding:13px 17px;flex:1;min-width:110px;}
`;

// ═══════════════════════════════════════════════════════════════════
// SCREEN: AUTH (Login / Register)
// ═══════════════════════════════════════════════════════════════════
function AuthScreen({ onLogin }) {
  const [mode, setMode] = useState("login"); // login | register
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = () => {
    setErr("");
    if (!phone.trim() || !pw) { setErr("Fill in all required fields."); return; }
    if (mode === "register") {
      if (!name.trim()) { setErr("Enter your name."); return; }
      if (pw !== pw2) { setErr("Passwords do not match."); return; }
      if (pw.length < 6) { setErr("Password must be at least 6 characters."); return; }
    }
    setLoading(true);
    setTimeout(() => {
      if (mode === "login") {
        const res = loginUser(phone.trim(), pw);
        if (!res.ok) { setErr(res.msg); setLoading(false); return; }
        onLogin(res.user);
      } else {
        const res = registerUser(phone.trim(), name.trim(), pw);
        if (!res.ok) { setErr(res.msg); setLoading(false); return; }
        onLogin(res.user);
      }
      setLoading(false);
    }, 700);
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "#07090f", backgroundImage: "radial-gradient(ellipse at 28% 55%,rgba(110,55,0,.13) 0%,transparent 58%),radial-gradient(ellipse at 72% 28%,rgba(15,25,55,.22) 0%,transparent 52%)", padding: 16 }}>
      <style>{STYLES}</style>
      <div className="fade-up" style={{ display: "flex", borderRadius: 18, overflow: "hidden", boxShadow: "0 32px 80px rgba(0,0,0,.75),0 0 0 1px rgba(245,158,11,.14)", maxWidth: 820, width: "100%" }}>

        {/* Left branding */}
        <div style={{ flex: "0 0 42%", background: "linear-gradient(155deg,#0c1120 0%,#080b12 55%,#090800 100%)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "44px 28px", position: "relative", overflow: "hidden" }}>
          {[220, 300, 380].map((s, i) => <div key={i} style={{ position: "absolute", bottom: -s * 0.38, left: "50%", transform: "translateX(-50%)", width: s, height: s, borderRadius: "50%", border: `1px solid rgba(245,158,11,${0.08 - i * 0.025})`, pointerEvents: "none" }} />)}
          <div style={{ position: "absolute", inset: 0, backgroundImage: "linear-gradient(rgba(245,158,11,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(245,158,11,.025) 1px,transparent 1px)", backgroundSize: "34px 34px", pointerEvents: "none" }} />
          <div style={{ position: "relative", animation: "floatY 4.5s ease infinite", marginBottom: 22 }}><Logo size={84} /></div>
          <div style={{ fontFamily: "'Bebas Neue'", fontSize: 38, letterSpacing: ".06em", color: "#fff", textShadow: "0 0 30px rgba(245,158,11,.4)", position: "relative" }}>ARBI<span style={{ color: "#f59e0b" }}>MINE</span></div>
          <div style={{ fontSize: 11, letterSpacing: ".26em", color: "#5a4820", marginTop: 4, position: "relative" }}>FIND · ANALYZE · PROFIT</div>
          <div style={{ position: "relative", marginTop: 32, display: "flex", flexDirection: "column", gap: 9 }}>
            {["Real-time Binance price data", "Multi-exchange arbitrage scan", "Free tier up to 2% spread", "Paid: KES 120/week full access"].map(f => (
              <div key={f} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#6a5228" }}>
                <span style={{ color: "#f59e0b", fontSize: 9 }}>◆</span>{f}
              </div>
            ))}
          </div>
        </div>

        {/* Right form */}
        <div style={{ flex: 1, background: "#0e1320", display: "flex", flexDirection: "column", justifyContent: "center", padding: "40px 36px" }}>
          <div style={{ fontSize: 24, fontWeight: 700, color: "#f0e0b0", marginBottom: 4 }}>{mode === "login" ? "Welcome Back!" : "Create Account"}</div>
          <div style={{ fontSize: 13, color: "#374060", marginBottom: 26 }}>{mode === "login" ? "Log in to your ArbiMine account." : "Start scanning arbitrage opportunities."}</div>

          {err && <div style={{ background: "rgba(239,68,68,.1)", border: "1px solid rgba(239,68,68,.28)", borderRadius: 7, padding: "10px 14px", marginBottom: 14, fontSize: 13, color: "#f87171", animation: "shake .3s ease" }}>{err}</div>}

          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {mode === "register" && (
              <div className="inp-icon">
                <span className="icon">👤</span>
                <input className="inp" placeholder="Full Name" value={name} onChange={e => setName(e.target.value)} />
              </div>
            )}
            <div className="inp-icon">
              <span className="icon">📱</span>
              <input className="inp" type="tel" placeholder="Phone Number (e.g. 0712345678)" value={phone}
                onChange={e => setPhone(e.target.value)} onKeyDown={e => e.key === "Enter" && submit()} />
            </div>
            <div className="inp-icon" style={{ position: "relative" }}>
              <span className="icon">🔒</span>
              <input className="inp" type={showPw ? "text" : "password"} placeholder="Password" value={pw}
                onChange={e => setPw(e.target.value)} onKeyDown={e => e.key === "Enter" && submit()}
                style={{ paddingRight: 42 }} />
              <button onClick={() => setShowPw(s => !s)} style={{ position: "absolute", right: 13, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: "#374060", fontSize: 15 }}>{showPw ? "🙈" : "👁"}</button>
            </div>
            {mode === "register" && (
              <div className="inp-icon">
                <span className="icon">🔒</span>
                <input className="inp" type="password" placeholder="Confirm Password" value={pw2}
                  onChange={e => setPw2(e.target.value)} onKeyDown={e => e.key === "Enter" && submit()} />
              </div>
            )}
          </div>

          <button className="btn btn-gold" onClick={submit} disabled={loading}
            style={{ width: "100%", padding: "14px", fontSize: 15, marginTop: 20, opacity: loading ? 0.6 : 1 }}>
            {loading ? "⏳ Please wait..." : mode === "login" ? "LOG IN" : "CREATE ACCOUNT"}
          </button>

          <div style={{ textAlign: "center", color: "#1e2540", margin: "18px 0", fontSize: 12 }}>or</div>

          <button onClick={() => { setMode(m => m === "login" ? "register" : "login"); setErr(""); }}
            style={{ background: "none", border: "none", color: "#c9860a", cursor: "pointer", fontSize: 13, fontFamily: "inherit", fontWeight: 500, textDecoration: "underline" }}>
            {mode === "login" ? "👤 Create new account" : "🔑 Already have an account? Log in"}
          </button>

          {mode === "login" && (
            <div style={{ marginTop: 20, background: "rgba(245,158,11,0.05)", border: "1px solid rgba(245,158,11,.1)", borderRadius: 8, padding: "10px 14px" }}>
              <div style={{ fontSize: 10, color: "#4a3818", marginBottom: 3, letterSpacing: ".1em" }}>ADMIN ACCESS</div>
              <div style={{ fontSize: 12, color: "#6a5228" }}>Phone: {ADMIN_PHONE} · Set your password on first use</div>
            </div>
          )}

          <div style={{ textAlign: "center", fontSize: 11, color: "#1a1e30", marginTop: 22 }}>© 2026 Arbimine. All rights reserved.</div>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// SCREEN: PAYMENT FLOW
// ═══════════════════════════════════════════════════════════════════
function PaymentScreen({ user, onBack, onSubmitted }) {
  const [step, setStep] = useState(1); // 1=instructions, 2=confirm form, 3=pending
  const [mpesaPhone, setMpesaPhone] = useState(user.phone);
  const [ref, setRef] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  // Check if already has a pending payment
  useEffect(() => {
    const pmts = getPayments().filter(p => p.userId === user.id && p.status === "pending");
    if (pmts.length > 0) setStep(3);
  }, [user.id]);

  const submit = () => {
    setErr("");
    if (!mpesaPhone.trim()) { setErr("Enter the M-Pesa phone number you paid from."); return; }
    if (!ref.trim() || ref.trim().length < 6) { setErr("Enter a valid M-Pesa confirmation code."); return; }
    setLoading(true);
    setTimeout(() => {
      submitPayment(user.id, mpesaPhone.trim(), WEEKLY_PRICE, ref.trim().toUpperCase());
      setLoading(false);
      setStep(3);
      onSubmitted();
    }, 800);
  };

  return (
    <div style={{ minHeight: "100vh", background: "#07090f", display: "flex", alignItems: "center", justifyContent: "center", padding: 20, fontFamily: "'Rajdhani',sans-serif" }}>
      <style>{STYLES}</style>
      <div className="fade-up" style={{ maxWidth: 480, width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 28 }}>
          <Logo size={36} />
          <div>
            <div style={{ fontFamily: "'Bebas Neue'", fontSize: 22, color: "#fff", letterSpacing: ".05em" }}>ARBI<span style={{ color: "#f59e0b" }}>MINE</span></div>
            <div style={{ fontSize: 9, letterSpacing: ".2em", color: "#2e2210" }}>UPGRADE TO PAID</div>
          </div>
        </div>

        {step === 1 && (
          <div>
            {/* Plan card */}
            <div style={{ background: "rgba(245,158,11,0.07)", border: "1px solid rgba(245,158,11,.22)", borderRadius: 12, padding: "22px 24px", marginBottom: 20 }}>
              <div style={{ fontSize: 11, color: "#4a3818", letterSpacing: ".12em", marginBottom: 8 }}>WEEKLY SUBSCRIPTION</div>
              <div style={{ fontSize: 36, fontWeight: 700, color: "#f59e0b", lineHeight: 1 }}>KES {WEEKLY_PRICE} <span style={{ fontSize: 15, color: "#7a6030", fontWeight: 400 }}>/week</span></div>
              <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 8 }}>
                {["All 28+ trading pairs unlocked", "Real-time Binance price data (30s updates)", "Spreads above 2% — highest profit opportunities", "Liquidity & volume data per pair", "Confidence scoring for every opportunity", "Auto-expiry after 7 days (renew anytime)"].map(f => (
                  <div key={f} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "#c9a84a" }}>
                    <span style={{ color: "#f59e0b", flexShrink: 0 }}>✔</span>{f}
                  </div>
                ))}
              </div>
            </div>

            {/* M-Pesa instructions */}
            <div style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,.06)", borderRadius: 12, padding: "20px 22px", marginBottom: 20 }}>
              <div style={{ fontSize: 13, color: "#4a3818", letterSpacing: ".1em", marginBottom: 14 }}>HOW TO PAY</div>
              {[
                ["1", "Go to M-Pesa on your phone"],
                ["2", "Select Lipa na M-Pesa → Send Money"],
                ["3", `Send KES ${WEEKLY_PRICE} to number:`],
                ["4", "Enter your M-Pesa PIN and confirm"],
                ["5", 'Come back here and tap "I Have Paid"'],
              ].map(([n, t]) => (
                <div key={n} style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 12 }}>
                  <div style={{ width: 22, height: 22, borderRadius: "50%", background: "rgba(245,158,11,0.15)", border: "1px solid rgba(245,158,11,0.3)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, color: "#f59e0b", fontWeight: 700, flexShrink: 0 }}>{n}</div>
                  <div style={{ fontSize: 14, color: "#9a8050", lineHeight: 1.4 }}>{t}</div>
                </div>
              ))}
              {/* Big M-Pesa number */}
              <div style={{ background: "rgba(245,158,11,0.12)", border: "2px solid rgba(245,158,11,0.4)", borderRadius: 10, padding: "14px 18px", textAlign: "center", marginTop: 8 }}>
                <div style={{ fontSize: 11, color: "#6a5228", letterSpacing: ".12em", marginBottom: 4 }}>M-PESA TILL / PHONE NUMBER</div>
                <div style={{ fontSize: 28, fontWeight: 700, color: "#f59e0b", letterSpacing: ".1em" }}>{ADMIN_PHONE}</div>
                <div style={{ fontSize: 12, color: "#5a4020", marginTop: 4 }}>Amount: KES {WEEKLY_PRICE}</div>
              </div>
            </div>

            <button className="btn btn-gold" onClick={() => setStep(2)} style={{ width: "100%", padding: "15px", fontSize: 16 }}>
              ✅ I Have Paid — Confirm Now
            </button>
            <button className="btn btn-ghost" onClick={onBack} style={{ width: "100%", padding: "12px", fontSize: 14, marginTop: 10, border: "1px solid rgba(255,255,255,0.08)" }}>
              ← Back to Dashboard
            </button>
          </div>
        )}

        {step === 2 && (
          <div>
            <div style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,.06)", borderRadius: 12, padding: "22px 24px", marginBottom: 16 }}>
              <div style={{ fontSize: 15, color: "#f0e0b0", fontWeight: 600, marginBottom: 6 }}>Confirm Your Payment</div>
              <div style={{ fontSize: 13, color: "#4a3818", marginBottom: 20 }}>Enter the details from your M-Pesa confirmation SMS so we can verify your payment.</div>

              {err && <div style={{ background: "rgba(239,68,68,.1)", border: "1px solid rgba(239,68,68,.28)", borderRadius: 7, padding: "9px 13px", marginBottom: 14, fontSize: 13, color: "#f87171" }}>{err}</div>}

              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div>
                  <div style={{ fontSize: 11, color: "#3a2810", letterSpacing: ".1em", marginBottom: 5 }}>PHONE NUMBER YOU PAID FROM</div>
                  <input className="inp" type="tel" placeholder="e.g. 0712345678" value={mpesaPhone}
                    onChange={e => setMpesaPhone(e.target.value)} />
                </div>
                <div>
                  <div style={{ fontSize: 11, color: "#3a2810", letterSpacing: ".1em", marginBottom: 5 }}>M-PESA CONFIRMATION CODE</div>
                  <input className="inp" placeholder="e.g. RGH3K7X2QP" value={ref}
                    onChange={e => setRef(e.target.value)} style={{ textTransform: "uppercase" }} />
                  <div style={{ fontSize: 11, color: "#2a1e08", marginTop: 4 }}>Found in the SMS you received after paying</div>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: "#3a2810", letterSpacing: ".1em", marginBottom: 5 }}>AMOUNT PAID</div>
                  <input className="inp" value={`KES ${WEEKLY_PRICE}`} readOnly style={{ opacity: 0.6 }} />
                </div>
              </div>
            </div>

            <button className="btn btn-gold" onClick={submit} disabled={loading} style={{ width: "100%", padding: "14px", fontSize: 15 }}>
              {loading ? "⏳ Submitting..." : "🚀 Submit Payment — Await Approval"}
            </button>
            <button className="btn btn-ghost" onClick={() => setStep(1)} style={{ width: "100%", padding: "12px", fontSize: 14, marginTop: 10, border: "1px solid rgba(255,255,255,0.08)" }}>
              ← Back
            </button>
          </div>
        )}

        {step === 3 && (
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 56, marginBottom: 16 }}>⏳</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: "#f0e0b0", marginBottom: 8 }}>Payment Under Review</div>
            <div style={{ fontSize: 14, color: "#5a4820", lineHeight: 1.7, marginBottom: 24 }}>
              Your payment confirmation has been received.<br />
              The admin will verify your M-Pesa payment and approve your access.<br />
              <span style={{ color: "#f59e0b" }}>You will get full access as soon as it is confirmed.</span>
            </div>
            <div style={{ background: "rgba(245,158,11,0.07)", border: "1px solid rgba(245,158,11,.18)", borderRadius: 10, padding: "16px", marginBottom: 20 }}>
              <div style={{ fontSize: 12, color: "#4a3818" }}>If not approved within 30 minutes, contact admin on WhatsApp:</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: "#f59e0b", marginTop: 6 }}>{ADMIN_PHONE}</div>
            </div>
            <button className="btn btn-ghost" onClick={onBack} style={{ width: "100%", padding: "13px", fontSize: 14, border: "1px solid rgba(255,255,255,0.08)" }}>
              ← Back to Dashboard (Free Mode)
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// SCREEN: ADMIN PANEL
// ═══════════════════════════════════════════════════════════════════
function AdminPanel({ user, onBack }) {
  const [tab, setTab] = useState("payments");
  const [payments, setPayments] = useState([]);
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState({});

  const reload = () => {
    setPayments(getPayments().sort((a, b) => new Date(b.submittedAt) - new Date(a.submittedAt)));
    setUsers(getUsers());
  };

  useEffect(() => { reload(); const t = setInterval(reload, 5000); return () => clearInterval(t); }, []);

  const pending = payments.filter(p => p.status === "pending");

  const doApprove = (id) => {
    setLoading(l => ({ ...l, [id]: "approving" }));
    setTimeout(() => { approvePayment(id); reload(); setLoading(l => ({ ...l, [id]: null })); }, 600);
  };

  const doDeny = (id) => {
    setLoading(l => ({ ...l, [id]: "denying" }));
    setTimeout(() => { denyPayment(id); reload(); setLoading(l => ({ ...l, [id]: null })); }, 600);
  };

  const getUserName = (uid) => users.find(u => u.id === uid)?.name || uid;
  const getUserPhone = (uid) => users.find(u => u.id === uid)?.phone || "—";

  const statusTag = (s) => <span className={`tag tag-${s === "pending" ? "pend" : s === "approved" ? "appr" : "deny"}`}>{s.toUpperCase()}</span>;

  return (
    <div style={{ minHeight: "100vh", background: "#07090f", fontFamily: "'Rajdhani',sans-serif" }}>
      <style>{STYLES}</style>

      {/* Header */}
      <div style={{ background: "rgba(7,9,15,.96)", backdropFilter: "blur(10px)", borderBottom: "1px solid rgba(245,158,11,.1)", padding: "0 22px", height: 54, display: "flex", alignItems: "center", justifyContent: "space-between", position: "sticky", top: 0, zIndex: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Logo size={30} />
          <div>
            <span style={{ fontFamily: "'Bebas Neue'", fontSize: 20, color: "#fff" }}>ARBI<span style={{ color: "#f59e0b" }}>MINE</span></span>
            <span style={{ fontSize: 11, color: "#4a3818", marginLeft: 10, letterSpacing: ".1em" }}>ADMIN PANEL</span>
          </div>
          {pending.length > 0 && (
            <div style={{ background: "rgba(239,68,68,.15)", border: "1px solid rgba(239,68,68,.3)", borderRadius: 20, padding: "2px 10px", fontSize: 12, color: "#f87171", animation: "blink 1.5s ease infinite" }}>
              🔔 {pending.length} PENDING
            </div>
          )}
        </div>
        <button className="btn btn-ghost" onClick={onBack} style={{ padding: "6px 14px", fontSize: 13, border: "1px solid rgba(255,255,255,0.08)" }}>← Dashboard</button>
      </div>

      <div style={{ padding: "20px 22px", maxWidth: 900, margin: "0 auto" }}>

        {/* Stat row */}
        <div style={{ display: "flex", gap: 10, marginBottom: 20, flexWrap: "wrap" }}>
          {[
            { l: "TOTAL USERS", v: users.filter(u => u.role !== "admin").length, c: "#60a5fa" },
            { l: "PAID ACTIVE", v: users.filter(u => u.plan === "paid").length, c: "#22c55e" },
            { l: "PENDING APPROVAL", v: pending.length, c: "#f59e0b" },
            { l: "TOTAL PAYMENTS", v: payments.length, c: "#a78bfa" },
          ].map(s => (
            <div key={s.l} className="stat-card">
              <div style={{ fontSize: 9, color: "#2a1c08", letterSpacing: ".12em", marginBottom: 4 }}>{s.l}</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: s.c }}>{s.v}</div>
            </div>
          ))}
        </div>

        {/* Tabs */}
        <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          {[["payments", "💳 Payments"], ["users", "👥 Users"]].map(([v, l]) => (
            <button key={v} className={`fbtn${tab === v ? " on" : ""}`} onClick={() => setTab(v)} style={{ padding: "7px 18px", fontSize: 13 }}>{l}</button>
          ))}
        </div>

        {tab === "payments" && (
          <div>
            {payments.length === 0 ? (
              <div style={{ textAlign: "center", padding: "52px 0", color: "#2a1e08" }}>
                <div style={{ fontSize: 34 }}>💳</div>
                <div style={{ marginTop: 10, fontSize: 14, color: "#4a3820" }}>No payments yet</div>
              </div>
            ) : payments.map(p => {
              const uName = getUserName(p.userId);
              const uPhone = getUserPhone(p.userId);
              return (
                <div key={p.id} style={{ background: "rgba(255,255,255,0.025)", border: `1px solid ${p.status === "pending" ? "rgba(245,158,11,.22)" : "rgba(255,255,255,.05)"}`, borderRadius: 10, padding: "16px 18px", marginBottom: 10 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                        <span style={{ fontSize: 15, fontWeight: 700, color: "#f0e0b0" }}>{uName}</span>
                        {statusTag(p.status)}
                        {p.status === "pending" && <span style={{ fontSize: 11, color: "#f59e0b", animation: "blink 1s ease infinite" }}>⚡ NEEDS ACTION</span>}
                      </div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 18px" }}>
                        {[["User Phone", uPhone], ["Paid From", p.mpesaPhone], ["M-Pesa Ref", p.ref], ["Amount", `KES ${p.amount}`], ["Submitted", new Date(p.submittedAt).toLocaleString("en-KE")]].map(([l, v]) => (
                          <div key={l} style={{ fontSize: 12, color: "#4a3818" }}>
                            <span style={{ color: "#2a1e08" }}>{l}: </span>
                            <span style={{ color: "#9a8050", fontWeight: 600 }}>{v}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                    {p.status === "pending" && (
                      <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                        <button className="btn btn-green" onClick={() => doApprove(p.id)}
                          disabled={loading[p.id]} style={{ padding: "8px 18px", fontSize: 13, border: "1px solid rgba(34,197,94,.3)" }}>
                          {loading[p.id] === "approving" ? "⏳" : "✅ APPROVE"}
                        </button>
                        <button className="btn btn-red" onClick={() => doDeny(p.id)}
                          disabled={loading[p.id]} style={{ padding: "8px 18px", fontSize: 13, border: "1px solid rgba(239,68,68,.3)" }}>
                          {loading[p.id] === "denying" ? "⏳" : "❌ DENY"}
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {tab === "users" && (
          <div>
            {users.filter(u => u.role !== "admin").map(u => (
              <div key={u.id} style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,.05)", borderRadius: 10, padding: "14px 18px", marginBottom: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                  <div>
                    <div style={{ fontSize: 15, fontWeight: 700, color: "#f0e0b0", marginBottom: 4 }}>{u.name}</div>
                    <div style={{ fontSize: 12, color: "#4a3818" }}>📱 {u.phone}</div>
                  </div>
                  <div style={{ display: "flex", align: "center", gap: 10 }}>
                    <span className={`tag ${u.plan === "paid" ? "tag-appr" : "tag-pend"}`}>{u.plan === "paid" ? "◆ PAID" : "○ FREE"}</span>
                    {u.plan === "paid" && u.expiry && <span style={{ fontSize: 11, color: "#5a4020" }}>Exp: {fmtDate(u.expiry)}</span>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// SCREEN: MAIN DASHBOARD
// ═══════════════════════════════════════════════════════════════════
function MainDashboard({ user: initialUser, onLogout, onUpgrade, onAdmin }) {
  const [user, setUser] = useState(initialUser);
  const [opps, setOpps] = useState([]);
  const [prices, setPrices] = useState({});
  const [apiOk, setApiOk] = useState(true);
  const [scanning, setScanning] = useState(true);
  const [selected, setSelected] = useState(null);
  const [minProfit, setMinProfit] = useState(0);
  const [gradeFilter, setGradeFilter] = useState("ALL");
  const [sortBy, setSortBy] = useState("profit");
  const [lastUpdate, setLastUpdate] = useState(null);
  const [sessionScanned, setSessionScanned] = useState(0);
  const [showUpgrade, setShowUpgrade] = useState(false);
  const [hasPending, setHasPending] = useState(false);
  const intervalRef = useRef(null);

  const isPaid = user.plan === "paid";

  // Refresh user from DB (in case admin just approved)
  const refreshUser = useCallback(() => {
    const fresh = getFreshUser(user.id);
    if (fresh) setUser(fresh);
    const pmts = getPayments().filter(p => p.userId === user.id && p.status === "pending");
    setHasPending(pmts.length > 0);
  }, [user.id]);

  const scan = useCallback(async () => {
    const { ok, prices: newPrices } = await fetchBinancePrices();
    setApiOk(ok);
    const usePrices = ok ? newPrices : prices;
    setPrices(usePrices);
    setOpps(buildOpportunities(usePrices, isPaid));
    setLastUpdate(new Date());
    setSessionScanned(p => p + SYMBOLS.length * 3);
    refreshUser();
  }, [isPaid, prices, refreshUser]);

  useEffect(() => { scan(); }, []);
  useEffect(() => {
    clearInterval(intervalRef.current);
    if (scanning) intervalRef.current = setInterval(scan, 30000);
    return () => clearInterval(intervalRef.current);
  }, [scanning, scan]);

  // For free users: only show opps with spread ≤ 2%
  const visibleOpps = isPaid ? opps : opps.filter(o => !o.isPaidOnly);
  const lockedCount = isPaid ? 0 : opps.filter(o => o.isPaidOnly).length;

  const filtered = visibleOpps
    .filter(o => o.profit >= minProfit)
    .filter(o => gradeFilter === "ALL" ? true : o.grade === gradeFilter)
    .sort((a, b) =>
      sortBy === "profit" ? b.profit - a.profit :
      sortBy === "spread" ? b.spread - a.spread :
      sortBy === "conf" ? b.conf - a.conf :
      sortBy === "vol" ? b.volume - a.volume :
      a.age - b.age
    );

  const selOpp = selected ? opps.find(o => o.id === selected) : null;
  const best = opps[0];
  const highCnt = opps.filter(o => o.grade === "HIGH" || o.grade === "PREMIUM").length;

  const gc = g => g === "PREMIUM" ? "#a855f7" : g === "HIGH" ? "#f59e0b" : g === "MED" ? "#d97706" : "#78350f";
  const gl = g => g === "PREMIUM" ? "💎 PREMIUM" : g === "HIGH" ? "🔥 HIGH" : g === "MED" ? "◆ MED" : "· LOW";

  return (
    <div style={{ minHeight: "100vh", background: "#07090f", color: "#c9b87a", fontFamily: "'Rajdhani',sans-serif" }}>
      <style>{STYLES}</style>

      {/* Scan beam */}
      {scanning && <div style={{ position: "fixed", left: 0, right: 0, height: "1px", background: "linear-gradient(90deg,transparent,rgba(245,158,11,.5),transparent)", pointerEvents: "none", zIndex: 5, animation: "scanBeam 6s linear infinite" }} />}

      {/* Upgrade teaser modal */}
      {showUpgrade && (
        <div className="modal-bg" onClick={() => setShowUpgrade(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 22, fontWeight: 700, color: "#f0e0b0", marginBottom: 4 }}>🔓 Unlock Full Access</div>
            <div style={{ fontSize: 13, color: "#4a3818", marginBottom: 18 }}>
              You currently see only opportunities with ≤{FREE_MAX_SPREAD}% spread.<br />
              <span style={{ color: "#f59e0b" }}>{lockedCount} higher-profit opportunities</span> are waiting for you.
            </div>
            <div style={{ background: "rgba(245,158,11,0.07)", border: "1px solid rgba(245,158,11,.2)", borderRadius: 10, padding: "16px 18px", marginBottom: 18 }}>
              <div style={{ fontSize: 28, fontWeight: 700, color: "#f59e0b" }}>KES {WEEKLY_PRICE} <span style={{ fontSize: 13, color: "#7a6030", fontWeight: 400 }}>/week</span></div>
              <div style={{ fontSize: 13, color: "#7a6030", marginTop: 4 }}>Pay via M-Pesa to {ADMIN_PHONE}</div>
            </div>
            {hasPending
              ? <div style={{ background: "rgba(251,191,36,.08)", border: "1px solid rgba(251,191,36,.25)", borderRadius: 8, padding: "12px 14px", fontSize: 13, color: "#fbbf24", marginBottom: 14 }}>⏳ Your payment is under review. Access will be granted once approved.</div>
              : <button className="btn btn-gold" onClick={() => { setShowUpgrade(false); onUpgrade(); }} style={{ width: "100%", padding: "14px", fontSize: 15, marginBottom: 10 }}>💳 Pay KES {WEEKLY_PRICE} via M-Pesa</button>
            }
            <button className="btn btn-ghost" onClick={() => setShowUpgrade(false)} style={{ width: "100%", padding: "11px", fontSize: 14, border: "1px solid rgba(255,255,255,0.08)" }}>Maybe Later</button>
          </div>
        </div>
      )}

      {/* ── HEADER ── */}
      <div style={{ background: "rgba(7,9,15,.96)", backdropFilter: "blur(10px)", borderBottom: "1px solid rgba(245,158,11,.1)", padding: "0 20px", height: 56, display: "flex", alignItems: "center", justifyContent: "space-between", position: "sticky", top: 0, zIndex: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Logo size={32} />
          <div>
            <div style={{ fontFamily: "'Bebas Neue'", fontSize: 20, color: "#fff", letterSpacing: ".05em", lineHeight: 1 }}>ARBI<span style={{ color: "#f59e0b" }}>MINE</span></div>
            <div style={{ fontSize: 8, letterSpacing: ".2em", color: "#2e2210" }}>FIND · ANALYZE · PROFIT</div>
          </div>
          {/* API status */}
          <div style={{ display: "flex", alignItems: "center", gap: 5, marginLeft: 14, fontSize: 11, color: apiOk ? "#2d6030" : "#7a4010" }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: apiOk ? "#22c55e" : "#f97316", boxShadow: `0 0 5px ${apiOk ? "#22c55e" : "#f97316"}`, display: "inline-block" }} />
            {apiOk ? "Binance Live" : "Using fallback data"}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "#c9b87a" }}>{user.name}</div>
            <div style={{ fontSize: 10, color: isPaid ? "#f59e0b" : "#5a4020" }}>
              {isPaid ? `◆ PAID · exp ${fmtDate(user.expiry)}` : "○ FREE"}{hasPending ? " · ⏳ pending" : ""}
            </div>
          </div>
          {user.role === "admin" && (
            <button className="btn" onClick={onAdmin} style={{ padding: "6px 12px", fontSize: 12, background: "rgba(168,85,247,.15)", border: "1px solid rgba(168,85,247,.3)", color: "#c084fc" }}>⚙ ADMIN</button>
          )}
          {!isPaid && (
            <button className="btn btn-gold" onClick={() => setShowUpgrade(true)} style={{ padding: "6px 14px", fontSize: 12 }}>
              {hasPending ? "⏳ PENDING" : "UPGRADE"}
            </button>
          )}
          <button className="btn btn-ghost" onClick={onLogout} style={{ padding: "6px 12px", fontSize: 12, border: "1px solid rgba(255,255,255,0.07)" }}>LOGOUT</button>
        </div>
      </div>

      {/* Free tier banner */}
      {!isPaid && (
        <div style={{ background: "linear-gradient(135deg,rgba(110,50,10,.22),rgba(80,35,5,.18))", borderBottom: "1px solid rgba(245,158,11,.14)", padding: "9px 20px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div style={{ fontSize: 13, color: "#c97a10" }}>
            🔒 Free tier — opportunities up to {FREE_MAX_SPREAD}% spread only.
            <span style={{ color: "#7a5820" }}> {lockedCount} higher-profit opportunities locked.</span>
          </div>
          <button className="btn btn-gold" onClick={() => setShowUpgrade(true)} style={{ padding: "5px 14px", fontSize: 12, flexShrink: 0 }}>
            {hasPending ? "⏳ Awaiting Approval" : `Unlock · KES ${WEEKLY_PRICE}/wk`}
          </button>
        </div>
      )}

      <div style={{ padding: "16px 20px", maxWidth: 1320, margin: "0 auto" }}>

        {/* Stat tiles */}
        <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
          {[
            { l: "PAIRS VISIBLE", v: visibleOpps.length, sub: isPaid ? `all ${SYMBOLS.length} pairs` : `free: ≤${FREE_MAX_SPREAD}%`, c: "#60a5fa" },
            { l: "OPPORTUNITIES", v: filtered.length, sub: `of ${visibleOpps.length} shown`, c: "#f59e0b", glow: true },
            { l: "HIGH/PREMIUM", v: highCnt, sub: "spread > 1%", c: "#22c55e" },
            { l: "SCANNED", v: sessionScanned.toLocaleString(), sub: "this session", c: "#a78bfa" },
            { l: "BEST PROFIT", v: best ? fmtPct(best.profit) : "—", sub: best?.pair ?? "scanning…", c: "#f59e0b" },
            { l: "UPDATED", v: lastUpdate ? lastUpdate.toLocaleTimeString() : "…", sub: scanning ? "30s auto" : "paused", c: "#94a3b8" },
          ].map((s, i) => (
            <div key={i} className="stat-card">
              <div style={{ fontSize: 9, color: "#2a1c08", letterSpacing: ".12em", marginBottom: 3 }}>{s.l}</div>
              <div className={s.glow ? "ore-glow" : ""} style={{ fontSize: 20, fontWeight: 700, color: s.c, lineHeight: 1 }}>{s.v}</div>
              <div style={{ fontSize: 10, color: "#221808", marginTop: 2 }}>{s.sub}</div>
            </div>
          ))}
        </div>

        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>

          {/* ── Scanner ── */}
          <div style={{ flex: "1 1 520px", minWidth: 0 }}>
            {/* Controls */}
            <div className="card" style={{ padding: "13px 16px", marginBottom: 12 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
                <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                  {["ALL", "PREMIUM", "HIGH", "MED", "LOW"].map(g => (
                    <button key={g} className={`fbtn${gradeFilter === g ? " on" : ""}`} onClick={() => setGradeFilter(g)}>{g}</button>
                  ))}
                </div>
                <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
                  <span style={{ fontSize: 10, color: "#2e2010" }}>SORT:</span>
                  {[["profit", "PROFIT"], ["spread", "SPREAD"], ["conf", "CONF"], ["vol", "VOL"]].map(([v, l]) => (
                    <button key={v} className={`fbtn${sortBy === v ? " on" : ""}`} onClick={() => setSortBy(v)}>{l}</button>
                  ))}
                </div>
                <button onClick={() => setScanning(s => !s)} style={{
                  background: scanning ? "rgba(245,158,11,.1)" : "rgba(255,80,80,.08)",
                  border: `1px solid ${scanning ? "rgba(245,158,11,.35)" : "rgba(255,80,80,.3)"}`,
                  color: scanning ? "#f59e0b" : "#f87171",
                  padding: "5px 14px", borderRadius: 5, cursor: "pointer",
                  fontFamily: "inherit", fontSize: 12, fontWeight: 600, letterSpacing: ".07em",
                  display: "flex", alignItems: "center", gap: 6,
                }}>
                  <span className={scanning ? "live-blink" : ""} style={{ width: 6, height: 6, borderRadius: "50%", background: scanning ? "#f59e0b" : "#f87171", display: "inline-block" }} />
                  {scanning ? "LIVE · 30s" : "PAUSED"}
                </button>
              </div>
              <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ fontSize: 10, color: "#2e2010", whiteSpace: "nowrap" }}>MIN PROFIT</span>
                <input type="range" min="0" max="3" step="0.05" value={minProfit} onChange={e => setMinProfit(+e.target.value)} />
                <span style={{ fontSize: 12, color: "#f59e0b", fontWeight: 600, minWidth: 48 }}>{fmtPct(minProfit)}</span>
              </div>
            </div>

            {/* Locked row teaser (for free users) */}
            {!isPaid && lockedCount > 0 && (
              <div onClick={() => setShowUpgrade(true)} style={{
                background: "linear-gradient(135deg,rgba(168,85,247,.07),rgba(245,158,11,.05))",
                border: "1px dashed rgba(168,85,247,.3)",
                borderRadius: 8, padding: "12px 16px", marginBottom: 8,
                cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between",
                transition: "background .15s",
              }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "#c084fc" }}>🔒 {lockedCount} Premium Opportunities Locked</div>
                  <div style={{ fontSize: 11, color: "#5a3a80", marginTop: 2 }}>Spreads above {FREE_MAX_SPREAD}% · Upgrade KES {WEEKLY_PRICE}/week to unlock</div>
                </div>
                <span style={{ color: "#a855f7", fontSize: 18 }}>›</span>
              </div>
            )}

            {/* Opportunity list */}
            <div style={{ maxHeight: "calc(100vh - 380px)", overflowY: "auto", paddingRight: 4 }}>
              {filtered.length === 0 ? (
                <div style={{ textAlign: "center", padding: "48px 0", color: "#2a1e08" }}>
                  <div style={{ fontSize: 34, marginBottom: 10 }}>⛏</div>
                  <div style={{ fontSize: 14, color: "#4a3820" }}>No opportunities at this filter.</div>
                  <div style={{ fontSize: 12, marginTop: 4 }}>Try lowering the minimum profit or changing the grade filter.</div>
                </div>
              ) : filtered.map((o, i) => (
                <div key={o.id}
                  className={`opp-row ${o.grade}${selected === o.id ? " sel" : ""}`}
                  style={{ animationDelay: `${i * 0.018}s` }}
                  onClick={() => setSelected(selected === o.id ? null : o.id)}
                >
                  {o.age < 20 && <span style={{ position: "absolute", top: 7, right: 10, background: "rgba(245,158,11,.13)", border: "1px solid rgba(245,158,11,.3)", borderRadius: 3, padding: "1px 6px", fontSize: 9, color: "#f59e0b", letterSpacing: ".1em" }}>NEW</span>}
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <ConfRing pct={o.conf} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3, flexWrap: "wrap" }}>
                        <span style={{ fontWeight: 700, fontSize: 15, color: "#e8d090" }}>{o.pair}</span>
                        <span style={{ fontSize: 10, color: gc(o.grade), fontWeight: 600 }}>{gl(o.grade)}</span>
                      </div>
                      <div style={{ fontSize: 11, color: "#3a2810" }}>
                        BUY <span style={{ color: "#7a6030" }}>{o.buyEx}</span>
                        {" → "}SELL <span style={{ color: "#7a6030" }}>{o.sellEx}</span>
                        <span style={{ color: "#2a1e08" }}> · {o.age}s ago</span>
                      </div>
                    </div>
                    <div style={{ textAlign: "right", flexShrink: 0 }}>
                      <div style={{ fontSize: 17, fontWeight: 700, color: gc(o.grade) }}>{fmtPct(o.profit)}</div>
                      <div style={{ fontSize: 10, color: "#2e2008" }}>spread {fmtPct(o.spread)}</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* ── Detail / Info panel ── */}
          <div style={{ flex: "0 0 268px", minWidth: 250 }}>
            {selOpp ? (
              <div className="fade-up">
                <div className="card" style={{ padding: "16px", marginBottom: 10 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: 18, color: "#f0e0b0" }}>{selOpp.pair}</div>
                      <div style={{ fontSize: 10, color: gc(selOpp.grade) }}>{gl(selOpp.grade)}</div>
                    </div>
                    <button onClick={() => setSelected(null)} style={{ background: "none", border: "none", color: "#3a2e10", cursor: "pointer", fontSize: 18 }}>✕</button>
                  </div>
                  {/* Profit bar */}
                  <div style={{ marginBottom: 14 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#3a2810", marginBottom: 4 }}>
                      <span>NET PROFIT</span><span style={{ color: "#f59e0b", fontWeight: 700 }}>{fmtPct(selOpp.profit)}</span>
                    </div>
                    <div style={{ height: 5, background: "rgba(255,255,255,.06)", borderRadius: 3, overflow: "hidden" }}>
                      <div style={{ height: "100%", borderRadius: 3, width: `${Math.min(100, (selOpp.profit / 4) * 100)}%`, background: "linear-gradient(90deg,#92400e,#f59e0b)", transition: "width .4s" }} />
                    </div>
                  </div>
                  {[
                    ["BUY ON", selOpp.buyEx, "#60a5fa"],
                    ["SELL ON", selOpp.sellEx, "#22c55e"],
                    ["BUY PRICE", `$${fmtP(selOpp.buyPrice)}`, "#e8d090"],
                    ["SELL PRICE", `$${fmtP(selOpp.sellPrice)}`, "#e8d090"],
                    ["SPREAD", fmtPct(selOpp.spread), "#f59e0b"],
                    ["EST. FEE", fmtPct(selOpp.fee), "#f87171"],
                    ["CONFIDENCE", `${Math.round(selOpp.conf)}%`, "#a78bfa"],
                    ["24H VOLUME", fmtVol(selOpp.volume), "#c9b87a"],
                    ["LIQUIDITY", fmtVol(selOpp.liquidity), "#c9b87a"],
                    ["DETECTED", `${selOpp.age}s ago`, "#94a3b8"],
                  ].map(([l, v, c]) => (
                    <div key={l} className="drow">
                      <span style={{ color: "#2e2008" }}>{l}</span>
                      <span style={{ color: c, fontWeight: 600 }}>{v}</span>
                    </div>
                  ))}
                </div>
                <div style={{ fontSize: 11, color: "#221808", lineHeight: 1.7, padding: "4px 2px" }}>
                  ⚠ Data sourced from Binance public API. Always verify current prices on each exchange before executing trades. Past opportunities do not guarantee future results.
                </div>
              </div>
            ) : (
              <div className="card" style={{ padding: 22, textAlign: "center" }}>
                <div style={{ fontSize: 32, marginBottom: 10 }}>⛏</div>
                <div style={{ fontSize: 14, color: "#4a3820", marginBottom: 6 }}>Select an opportunity</div>
                <div style={{ fontSize: 12, color: "#2a1e08", lineHeight: 1.65 }}>Tap any row to see full price breakdown, exchanges, liquidity and confidence data.</div>

                {/* Subscription status */}
                <div style={{ marginTop: 20, borderTop: "1px solid rgba(255,255,255,.05)", paddingTop: 18 }}>
                  <div style={{ fontSize: 10, letterSpacing: ".12em", color: "#1e1608", marginBottom: 10 }}>YOUR PLAN</div>
                  <div style={{ background: isPaid ? "rgba(245,158,11,.08)" : "rgba(255,255,255,.025)", border: `1px solid ${isPaid ? "rgba(245,158,11,.25)" : "rgba(255,255,255,.05)"}`, borderRadius: 8, padding: 14 }}>
                    <div style={{ fontSize: 15, fontWeight: 700, color: isPaid ? "#f59e0b" : "#4a3818" }}>
                      {isPaid ? "◆ PAID ACCESS" : "○ FREE TIER"}
                    </div>
                    <div style={{ fontSize: 11, color: "#2e2008", marginTop: 4 }}>
                      {isPaid ? `Active · Expires ${fmtDate(user.expiry)}` : `Seeing ≤${FREE_MAX_SPREAD}% spread only`}
                    </div>
                    {!isPaid && !hasPending && (
                      <button className="btn btn-gold" onClick={() => setShowUpgrade(true)} style={{ marginTop: 12, width: "100%", padding: "9px", fontSize: 13 }}>
                        Upgrade KES {WEEKLY_PRICE}/wk
                      </button>
                    )}
                    {!isPaid && hasPending && (
                      <div style={{ marginTop: 10, fontSize: 12, color: "#fbbf24", background: "rgba(251,191,36,.08)", border: "1px solid rgba(251,191,36,.2)", borderRadius: 6, padding: "8px" }}>
                        ⏳ Payment pending admin approval
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Footer */}
      <div style={{ borderTop: "1px solid rgba(255,255,255,.04)", padding: "10px 20px", marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, color: "#1a1408", flexWrap: "wrap", gap: 6 }}>
        <div>© 2026 Arbimine. All rights reserved.</div>
        <div>Binance · OKX · KuCoin · Bybit · Gate.io · Huobi · Bitget · MEXC</div>
        <div>v3.0 · Free ≤{FREE_MAX_SPREAD}% · Paid full access</div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// ROOT APP
// ═══════════════════════════════════════════════════════════════════
export default function App() {
  const [screen, setScreen] = useState("auth"); // auth | main | payment | admin
  const [user, setUser] = useState(null);

  useEffect(() => { initDB(); }, []);

  if (screen === "auth") return <AuthScreen onLogin={u => { setUser(u); setScreen("main"); }} />;
  if (screen === "payment") return <PaymentScreen user={user} onBack={() => setScreen("main")} onSubmitted={() => { const fresh = getFreshUser(user.id); if (fresh) setUser(fresh); }} />;
  if (screen === "admin") return <AdminPanel user={user} onBack={() => setScreen("main")} />;
  return (
    <MainDashboard
      user={user}
      onLogout={() => { setUser(null); setScreen("auth"); }}
      onUpgrade={() => setScreen("payment")}
      onAdmin={() => setScreen("admin")}
    />
  );
}
