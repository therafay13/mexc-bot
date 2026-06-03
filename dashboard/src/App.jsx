import React, { useState, useEffect, useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import { useWebSocket } from './hooks/useWebSocket';

const C = {
  bg: "#050810", surface: "#0b0f1a", card: "#0f1420", border: "#1a2236",
  accent: "#00e5ff", accentDim: "#00e5ff22",
  green: "#00e676", greenDim: "#00e67622",
  red: "#ff1744",   redDim: "#ff174422",
  yellow: "#ffd600", purple: "#7b2fff",
  text: "#e2e8f0", muted: "#4a5568", dim: "#1e2a3a",
};

const FONTS = {
  main: "'Space Grotesk', sans-serif",
  mono: "'IBM Plex Mono', monospace",
};

const App = () => {
  const [tab, setTab] = useState('Overview');
  const [status, setStatus] = useState({ running: true, kronos_loaded: false, mode: 'PAPER' });
  const [account, setAccount] = useState({ balance: 300, day_open: 300, daily_pnl: 0, total_pnl: 0, win_rate: 0, kronos_acc: 0, equity_curve: [] });
  const [positions, setPositions] = useState([]);
  const [pendingOrders, setPendingOrders] = useState([]);
  const [signals, setSignals] = useState([]);
  const [trades, setTrades] = useState([]);
  const [scanner, setScanner] = useState([]);
  const [events, setEvents] = useState([]);
  const [nextScan, setNextScan] = useState(300);
  const [settings, setSettings] = useState({
    MEXC_API_KEY: '',
    MEXC_SECRET_KEY: '',
    PAPER_TRADING: true,
    KRONOS_MODEL: 'small',
    KRONOS_DEVICE: 'cpu',
  });

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        setSettings(data);
      } catch (e) {}
    };
    fetchSettings();
  }, []);

  useEffect(() => {
    const style = document.createElement('style');
    style.innerHTML = `
      @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Space+Grotesk:wght@400;600;700&display=swap');
      body { margin: 0; background: ${C.bg}; color: ${C.text}; font-family: ${FONTS.main}; }
      .mono { font-family: ${FONTS.mono}; }
    `;
    document.head.appendChild(style);
  }, []);

  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch('/api/account');
        const data = await res.json();
        setAccount(data);
      } catch (e) {}
    }, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const fetchInit = async () => {
      try {
        const s = await fetch('/api/status').then(r => r.json());
        setStatus({ ...status, ...s });
        const p = await fetch('/api/positions').then(r => r.json());
        setPositions(p);
        const pend = await fetch('/api/pending').then(r => r.json());
        setPendingOrders(pend);
        const sig = await fetch('/api/signals').then(r => r.json());
        setSignals(sig);
        const t = await fetch('/api/trades').then(r => r.json());
        setTrades(t);
      } catch (e) {}
    };
    fetchInit();
  }, []);

  useEffect(() => {
    const timer = setInterval(() => setNextScan(prev => prev > 0 ? prev - 1 : 300), 1000);
    return () => clearInterval(timer);
  }, []);

  useWebSocket('ws://localhost:8000/ws/live', (msg) => {
    const { event, data } = msg;
    if (event === 'connected') {
      setStatus(prev => ({ ...prev, kronos_loaded: data.kronos_loaded, mode: data.mode }));
    } else if (event === 'price') {
      setPositions(prev => prev.map(p => p.symbol === data.symbol ? { ...p, current_price: data.price } : p));
    } else if (event === 'signal') {
      setSignals(prev => [ { ...data, timestamp: msg.ts }, ...prev ].slice(0, 30));
      setEvents(prev => [ { ts: new Date(msg.ts * 1000).toLocaleTimeString(), type: 'signal', msg: `Signal: ${data.symbol} ${data.direction} (Score: ${data.score})`, color: C.accent }, ...prev ].slice(0, 100));
    } else if (event === 'pending_order') {
      const pend = { ...data, entry_price: data.entry, sl_price: data.sl };
      setPendingOrders(prev => [...prev, pend]);
      setEvents(prev => [ { ts: new Date(msg.ts * 1000).toLocaleTimeString(), type: 'pending', msg: `Pending Order: ${data.symbol} ${data.direction} @ ${data.entry} ${data.leverage}x`, color: C.yellow }, ...prev ].slice(0, 100));
    } else if (event === 'trade_opened') {
      setEvents(prev => [ { ts: new Date(msg.ts * 1000).toLocaleTimeString(), type: 'trade', msg: `Trade Opened: ${data.symbol} ${data.direction}`, color: C.green }, ...prev ].slice(0, 100));
      setPendingOrders(prev => prev.filter(p => p.symbol !== data.symbol));
      fetch('/api/positions').then(r => r.json()).then(setPositions);
    } else if (event === 'scanner_update') {
      setScanner(data.coins);
    }
  });

  const toggleBot = async () => {
    const endpoint = status.running ? '/api/bot/stop' : '/api/bot/start';
    await fetch(endpoint, { method: 'POST' });
    setStatus(prev => ({ ...prev, running: !prev.running }));
  };

  const saveSettings = async () => {
    try {
      await fetch('/api/bot/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });
      alert('Settings saved! Please restart the bot for changes to take effect.');
    } catch (e) {
      alert('Failed to save settings.');
    }
  };

  const cancelOrder = async (symbol) => {
    try {
      const res = await fetch(`/api/orders/${symbol}/cancel`, { method: 'POST' });
      const data = await res.json();
      if (data.status === 'cancelled') {
        setPendingOrders(prev => prev.filter(p => p.symbol !== symbol));
        setEvents(prev => [{ ts: new Date().toLocaleTimeString(), type: 'cancel', msg: `Cancelled: ${symbol}`, color: C.red }, ...prev].slice(0, 100));
      }
    } catch (e) {}
  };

  const chaseOrder = async (symbol) => {
    try {
      const res = await fetch(`/api/orders/${symbol}/chase`, { method: 'POST' });
      const data = await res.json();
      if (data.status === 'executed') {
        setPendingOrders(prev => prev.filter(p => p.symbol !== symbol));
        setEvents(prev => [{ ts: new Date().toLocaleTimeString(), type: 'chase', msg: `Chased: ${symbol} @ ${data.entry_price}`, color: C.green }, ...prev].slice(0, 100));
        setTimeout(async () => {
          const p = await fetch('/api/positions').then(r => r.json());
          setPositions(p);
        }, 500);
      }
    } catch (e) {}
  };

  const closePosition = async (symbol) => {
    if (!window.confirm(`Close ${symbol} at market price?`)) return;
    try {
      const res = await fetch(`/api/positions/${symbol}/close`, { method: 'POST' });
      const data = await res.json();
      if (data.status === 'closed') {
        setPositions(prev => prev.filter(p => p.symbol !== symbol));
        setEvents(prev => [{ ts: new Date().toLocaleTimeString(), type: 'close', msg: `Closed: ${symbol}`, color: C.red }, ...prev].slice(0, 100));
        setTimeout(async () => {
          const t = await fetch('/api/trades').then(r => r.json());
          setTrades(t);
        }, 500);
      }
    } catch (e) {}
  };

  const SidebarItem = ({ label }) => (
    <div 
      onClick={() => setTab(label)} 
      style={{ 
        padding: '12px 20px', 
        cursor: 'pointer', 
        color: tab === label ? C.accent : C.muted,
        backgroundColor: tab === label ? C.accentDim : 'transparent',
        borderLeft: tab === label ? `3px solid ${C.accent}` : '3px solid transparent',
        transition: '0.2s',
        fontWeight: 600,
        fontSize: '14px'
      }}
    >
      {label}
    </div>
  );

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      {/* Sidebar */}
      <div style={{ width: '240px', backgroundColor: C.surface, borderRight: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '20px', fontSize: '20px', fontWeight: 700, color: C.text, display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div style={{ width: '24px', height: '24px', background: `linear-gradient(135deg, ${C.accent}, ${C.purple})`, borderRadius: '4px' }}></div>
          SMCbot
        </div>
        <div style={{ flex: 1, marginTop: '20px' }}>
          {['Overview', 'Positions', 'Signals', 'History', 'Scanner', 'Settings'].map(item => <SidebarItem key={item} label={item} />)}
        </div>
        <div style={{ padding: '20px', borderTop: `1px solid ${C.border}`, backgroundColor: C.card }}>
          <div className="mono" style={{ fontSize: '22px', fontWeight: 600, marginBottom: '8px' }}>${account.balance.toFixed(2)}</div>
          <div className="mono" style={{ fontSize: '14px', color: account.daily_pnl >= 0 ? C.green : C.red }}>
            {account.daily_pnl >= 0 ? '+' : ''}{account.daily_pnl.toFixed(2)} USDT
          </div>
          <div style={{ 
            marginTop: '12px', 
            display: 'inline-block', 
            padding: '2px 8px', 
            borderRadius: '4px', 
            fontSize: '10px', 
            fontWeight: 700, 
            backgroundColor: status.mode === 'PAPER' ? C.yellow : C.green, 
            color: C.bg 
          }}>
            {status.mode}
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Top Bar */}
        <div style={{ height: '56px', backgroundColor: C.surface, borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '15px', fontSize: '13px', color: C.muted }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: status.running ? C.green : C.red, boxShadow: status.running ? `0 0 8px ${C.green}` : 'none' }}></div>
              <span style={{ color: C.text, fontWeight: 600 }}>{status.running ? 'LIVE' : 'STOPPED'}</span>
            </div>
            <div style={{ width: '1px', height: '14px', backgroundColor: C.border }}></div>
            <span>MEXC Futures</span>
            <div style={{ width: '1px', height: '14px', backgroundColor: C.border }}></div>
            <span style={{ 
              padding: '2px 6px', 
              borderRadius: '4px', 
              backgroundColor: status.kronos_loaded ? C.purple : C.dim, 
              color: '#fff', 
              fontSize: '10px', 
              fontWeight: 700 
            }}>
              🧠 KRONOS {status.kronos_loaded ? 'ACTIVE' : 'OFFLINE'}
            </span>
          </div>
          <button 
            onClick={toggleBot}
            style={{ 
              backgroundColor: status.running ? C.redDim : C.greenDim, 
              color: status.running ? C.red : C.green, 
              border: `1px solid ${status.running ? C.red : C.green}`,
              padding: '6px 12px', 
              borderRadius: '4px', 
              cursor: 'pointer', 
              fontSize: '12px', 
              fontWeight: 600,
              transition: '0.2s'
            }}
          >
            {status.running ? 'STOP BOT' : 'START BOT'}
          </button>
        </div>

        {/* Tab Content */}
        <div style={{ flex: 1, padding: '24px', overflowY: 'auto' }}>
          {tab === 'Overview' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '20px' }}>
                {[
                  { label: 'Total PnL', val: `$${account.total_pnl.toFixed(2)}`, color: account.total_pnl >= 0 ? C.green : C.red },
                  { label: 'Day PnL', val: `$${account.daily_pnl.toFixed(2)}`, color: account.daily_pnl >= 0 ? C.green : C.red },
                  { label: 'Win Rate', val: `${account.win_rate}%`, color: C.accent },
                  { label: 'Active', val: `${positions.length + pendingOrders.length}/3`, color: C.text },
                ].map((card, i) => (
                  <div key={i} style={{ backgroundColor: C.card, padding: '20px', borderRadius: '8px', border: `1px solid ${C.border}` }}>
                    <div style={{ color: C.muted, fontSize: '12px', marginBottom: '8px' }}>{card.label}</div>
                    <div className="mono" style={{ fontSize: '24px', fontWeight: 700, color: card.color }}>{card.val}</div>
                  </div>
                ))}
              </div>
              
              <div style={{ backgroundColor: C.card, padding: '20px', borderRadius: '8px', border: `1px solid ${C.border}`, height: '300px' }}>
                <div style={{ marginBottom: '20px', fontWeight: 600 }}>Equity Curve</div>
                <ResponsiveContainer width="100%" height="90%">
                  <LineChart data={account.equity_curve}>
                    <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                    <XAxis dataKey="day" stroke={C.muted} fontSize={10} tickLine={false} axisLine={false} />
                    <YAxis stroke={C.muted} fontSize={10} tickLine={false} axisLine={false} domain={['auto', 'auto']} />
                    <Tooltip contentStyle={{ backgroundColor: C.surface, border: `1px solid ${C.border}`, color: C.text }} />
                    <ReferenceLine y={300} stroke={C.muted} strokeDasharray="3 3" />
                    <Line type="monotone" dataKey="equity" stroke={C.accent} strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
                <div style={{ backgroundColor: C.card, padding: '20px', borderRadius: '8px', border: `1px solid ${C.border}` }}>
                  <div style={{ marginBottom: '15px', fontWeight: 600 }}>Event Feed</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '300px', overflowY: 'auto' }}>
                    {events.map((e, i) => (
                      <div key={i} style={{ fontSize: '12px', display: 'flex', gap: '10px', padding: '4px 0', borderBottom: `1px solid ${C.dim}` }}>
                        <span className="mono" style={{ color: C.muted }}>{e.ts}</span>
                        <span style={{ color: e.color }}>{e.msg}</span>
                      </div>
                    ))}
                    {events.length === 0 && <div style={{ color: C.muted, fontSize: '12px' }}>No events yet...</div>}
                  </div>
                </div>
                <div style={{ backgroundColor: C.card, padding: '20px', borderRadius: '8px', border: `1px solid ${C.border}` }}>
                  <div style={{ marginBottom: '15px', fontWeight: 600 }}>Kronos Status</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', fontSize: '13px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: C.muted }}>Model Loaded</span>
                      <span style={{ color: status.kronos_loaded ? C.green : C.red }}>{status.kronos_loaded ? 'YES' : 'NO'}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: C.muted }}>Device</span>
                      <span className="mono">{status.device || 'cpu'}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: C.muted }}>Accuracy</span>
                      <span className="mono" style={{ color: C.purple }}>{account.kronos_acc}%</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {tab === 'Positions' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
              {pendingOrders.length > 0 && (
                <>
                  <div style={{ color: C.yellow, fontWeight: 600, fontSize: '14px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v4l3 3"/></svg>
                    PENDING ORDERS ({pendingOrders.length})
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '16px' }}>
                    {pendingOrders.map((p, i) => (
                      <div key={i} style={{ backgroundColor: C.card, padding: '20px', borderRadius: '8px', border: `1px dashed ${C.yellow}44`, position: 'relative' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                            <span style={{ fontSize: '18px', fontWeight: 700 }}>{p.symbol}</span>
                            <span style={{ padding: '2px 6px', borderRadius: '4px', fontSize: '10px', fontWeight: 700, backgroundColor: p.direction === 'LONG' ? C.greenDim : C.redDim, color: p.direction === 'LONG' ? C.green : C.red }}>{p.direction}</span>
                            <span style={{ padding: '2px 6px', borderRadius: '4px', fontSize: '10px', backgroundColor: C.dim, color: C.text }}>{p.leverage}x</span>
                          </div>
                          <span style={{ fontSize: '11px', color: C.yellow, fontWeight: 700, opacity: 0.7 }}>⏳ PENDING</span>
                        </div>
                        <div style={{ textAlign: 'center', fontSize: '18px', fontWeight: 700, color: C.yellow, opacity: 0.7, margin: '12px 0' }}>Limit @ {p.entry_price}</div>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px', fontSize: '11px', textAlign: 'center', marginBottom: '12px' }}>
                          <div><div style={{ color: C.muted, marginBottom: '4px' }}>SL</div><div className="mono">{p.sl_price}</div></div>
                          <div><div style={{ color: C.muted, marginBottom: '4px' }}>TP1</div><div className="mono">{p.tp1}</div></div>
                          <div><div style={{ color: C.muted, marginBottom: '4px' }}>Score</div><div className="mono">{p.score}</div></div>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: C.muted }}>
                          <span>{p.direction === 'LONG' ? 'Waiting for drop to entry' : 'Waiting for rise to entry'}</span>
                          <span className="mono">{p.remaining_sec > 0 ? `${Math.floor(p.remaining_sec / 60)}m ${p.remaining_sec % 60}s` : 'Expiring'}</span>
                        </div>
                        <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                          <button onClick={() => cancelOrder(p.symbol)} style={{ flex: 1, padding: '6px 0', fontSize: '11px', fontWeight: 600, backgroundColor: `${C.red}22`, color: C.red, border: `1px solid ${C.red}44`, borderRadius: '4px', cursor: 'pointer' }}>Cancel</button>
                          <button onClick={() => chaseOrder(p.symbol)} style={{ flex: 1, padding: '6px 0', fontSize: '11px', fontWeight: 600, backgroundColor: `${C.green}22`, color: C.green, border: `1px solid ${C.green}44`, borderRadius: '4px', cursor: 'pointer' }}>Chase</button>
                        </div>
                        {p.flow && <div style={{ fontSize: '10px', color: C.muted, textAlign: 'center', marginTop: '8px' }}>Flow: {p.flow} {p.entry_reason ? `| ${p.entry_reason}` : ''}</div>}
                      </div>
                    ))}
                  </div>
                </>
              )}
              <div style={{ color: C.text, fontWeight: 600, fontSize: '14px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                OPEN POSITIONS ({positions.length})
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(350px, 1fr))', gap: '20px' }}>
                {positions.map((p, i) => (
                  <div key={i} style={{ backgroundColor: C.card, padding: '20px', borderRadius: '8px', border: `1px solid ${C.border}`, position: 'relative' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <span style={{ fontSize: '18px', fontWeight: 700 }}>{p.symbol}</span>
                        <span style={{ padding: '2px 6px', borderRadius: '4px', fontSize: '10px', fontWeight: 700, backgroundColor: p.direction === 'LONG' ? C.greenDim : C.redDim, color: p.direction === 'LONG' ? C.green : C.red }}>{p.direction}</span>
                        <span style={{ padding: '2px 6px', borderRadius: '4px', fontSize: '10px', backgroundColor: C.dim, color: C.text }}>{p.leverage}x</span>
                        {p.tp1_hit && <span style={{ padding: '2px 6px', borderRadius: '4px', fontSize: '10px', backgroundColor: C.accentDim, color: C.accent, fontWeight: 700 }}>BE ✓</span>}
                      </div>
                      <button onClick={() => closePosition(p.symbol)} style={{ padding: '4px 10px', fontSize: '10px', fontWeight: 600, backgroundColor: `${C.red}22`, color: C.red, border: `1px solid ${C.red}44`, borderRadius: '4px', cursor: 'pointer' }}>Close</button>
                    </div>
                    <div className="mono" style={{ fontSize: '32px', fontWeight: 700, textAlign: 'center', margin: '20px 0', color: p.unrealized_pnl >= 0 ? C.green : C.red }}>
                      {p.unrealized_pnl >= 0 ? '+' : ''}{p.unrealized_pnl.toFixed(2)}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px', fontSize: '11px', textAlign: 'center', marginBottom: '20px' }}>
                      <div><div style={{ color: C.muted, marginBottom: '4px' }}>Entry</div><div className="mono">{p.entry_price}</div></div>
                      <div><div style={{ color: C.muted, marginBottom: '4px' }}>Current</div><div className="mono">{p.current_price}</div></div>
                      <div><div style={{ color: C.muted, marginBottom: '4px' }}>SL</div><div className="mono">{p.sl_price}</div></div>
                      <div><div style={{ color: C.muted, marginBottom: '4px' }}>TP1</div><div className="mono">{p.tp1}</div></div>
                    </div>
                    <div style={{ height: '6px', backgroundColor: C.dim, borderRadius: '3px', overflow: 'hidden', marginBottom: '20px', position: 'relative' }}>
                      <div style={{ position: 'absolute', left: '50%', width: '2px', height: '100%', backgroundColor: C.text, zIndex: 2 }}></div>
                      <div style={{ position: 'absolute', left: '0', width: '100%', height: '100%', background: `linear-gradient(to right, ${C.red}, ${C.yellow}, ${C.green})`, opacity: 0.6 }}></div>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ color: C.muted }}>Score:</span>
                        <div style={{ width: '60px', height: '4px', backgroundColor: C.dim, borderRadius: '2px' }}>
                          <div style={{ width: `${p.score}%`, height: '100%', backgroundColor: C.accent, borderRadius: '2px' }}></div>
                        </div>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ color: C.muted }}>Kronos:</span>
                        <div style={{ width: '60px', height: '4px', backgroundColor: C.dim, borderRadius: '2px' }}>
                          <div style={{ width: `${p.kronos_confidence * 100}%`, height: '100%', backgroundColor: C.purple, borderRadius: '2px' }}></div>
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
                {[...Array(Math.max(0, 3 - positions.length))].map((_, i) => (
                  <div key={i} style={{ backgroundColor: C.surface, padding: '40px', borderRadius: '8px', border: `1px dashed ${C.border}`, textAlign: 'center', color: C.muted, fontSize: '14px' }}>
                    Empty Slot
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === 'Signals' && (
            <div style={{ backgroundColor: C.card, borderRadius: '8px', border: `1px solid ${C.border}`, overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px', textAlign: 'left' }}>
                <thead>
                  <tr style={{ backgroundColor: C.surface, color: C.muted, fontWeight: 600 }}>
                    <th style={{ padding: '12px 20px' }}>Symbol</th>
                    <th style={{ padding: '12px 20px' }}>Dir</th>
                    <th style={{ padding: '12px 20px' }}>Score</th>
                    <th style={{ padding: '12px 20px' }}>Kronos</th>
                    <th style={{ padding: '12px 20px' }}>Flow</th>
                    <th style={{ padding: '12px 20px' }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {signals.map((s, i) => (
                    <tr key={i} style={{ borderBottom: `1px solid ${C.border}` }}>
                      <td style={{ padding: '12px 20px', fontWeight: 600 }}>{s.symbol}</td>
                      <td style={{ padding: '12px 20px', color: s.direction === 'LONG' ? C.green : C.red }}>{s.direction}</td>
                      <td style={{ padding: '12px 20px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span className="mono">{s.score}</span>
                          <div style={{ width: '40px', height: '4px', backgroundColor: C.dim, borderRadius: '2px' }}>
                            <div style={{ width: `${s.score}%`, height: '100%', backgroundColor: C.accent, borderRadius: '2px' }}></div>
                          </div>
                        </div>
                      </td>
                      <td style={{ padding: '12px 20px' }}>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                          <span className="mono" style={{ fontSize: '11px' }}>{s.kronos_move_pct}%</span>
                          <div style={{ width: '60px', height: '4px', backgroundColor: C.dim, borderRadius: '2px' }}>
                            <div style={{ width: `${(s.kronos_confidence || 0) * 100}%`, height: '100%', backgroundColor: C.purple, borderRadius: '2px' }}></div>
                          </div>
                          {s.kronos_vetoed && <span style={{ color: C.red, fontSize: '9px', fontWeight: 700 }}>VETOED</span>}
                        </div>
                      </td>
                      <td style={{ padding: '12px 20px' }}>
                        {s.flow ? 
                          <span style={{ 
                            display: 'inline-block', padding: '2px 8px', borderRadius: '4px', 
                            fontSize: '10px', fontWeight: 700,
                            backgroundColor: s.flow === 'CONFIRMS' ? C.greenDim : s.flow === 'NEUTRAL' ? '#ffd60022' : C.redDim,
                            color: s.flow === 'CONFIRMS' ? C.green : s.flow === 'NEUTRAL' ? C.yellow : C.red
                          }}>{s.flow}</span> : 
                          <span style={{ color: C.muted, fontSize: '11px' }}>—</span>
                        }
                      </td>
                      <td style={{ padding: '12px 20px' }}>
                        {s.approved ? 
                          <span style={{ color: C.green, fontSize: '11px', fontWeight: 700 }}>✓ APPROVED</span> : 
                          <span style={{ color: C.red, fontSize: '11px' }}>✗ {s.reason}</span>
                        }
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {tab === 'History' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '20px' }}>
                {[
                  { label: 'Total Closed', val: trades.length, color: C.text },
                  { label: 'Winners', val: trades.filter(t => t.winner).length, color: C.green },
                  { label: 'Losers', val: trades.filter(t => !t.winner).length, color: C.red },
                  { label: 'Net PnL', val: `$${trades.reduce((acc, t) => acc + t.pnl, 0).toFixed(2)}`, color: C.text },
                ].map((card, i) => (
                  <div key={i} style={{ backgroundColor: C.card, padding: '20px', borderRadius: '8px', border: `1px solid ${C.border}` }}>
                    <div style={{ color: C.muted, fontSize: '12px', marginBottom: '8px' }}>{card.label}</div>
                    <div className="mono" style={{ fontSize: '24px', fontWeight: 700, color: card.color }}>{card.val}</div>
                  </div>
                ))}
              </div>
              <div style={{ backgroundColor: C.card, borderRadius: '8px', border: `1px solid ${C.border}`, overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px', textAlign: 'left' }}>
                  <thead>
                    <tr style={{ backgroundColor: C.surface, color: C.muted, fontWeight: 600 }}>
                      <th style={{ padding: '12px 20px' }}>Symbol</th>
                      <th style={{ padding: '12px 20px' }}>Dir</th>
                      <th style={{ padding: '12px 20px' }}>Entry</th>
                      <th style={{ padding: '12px 20px' }}>Exit</th>
                      <th style={{ padding: '12px 20px' }}>PnL</th>
                      <th style={{ padding: '12px 20px' }}>R:R</th>
                      <th style={{ padding: '12px 20px' }}>Score</th>
                      <th style={{ padding: '12px 20px' }}>Kronos</th>
                      <th style={{ padding: '12px 20px' }}>When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((t, i) => (
                      <tr key={i} style={{ borderBottom: `1px solid ${C.border}` }}>
                        <td style={{ padding: '12px 20px', fontWeight: 600 }}>{t.symbol}</td>
                        <td style={{ padding: '12px 20px', color: t.direction === 'LONG' ? C.green : C.red }}>{t.direction}</td>
                        <td style={{ padding: '12px 20px', className: 'mono' }}>{t.entry}</td>
                        <td style={{ padding: '12px 20px', className: 'mono' }}>{t.exit}</td>
                        <td style={{ padding: '12px 20px', className: 'mono', color: t.pnl >= 0 ? C.green : C.red }}>{t.pnl.toFixed(2)}</td>
                        <td style={{ padding: '12px 20px', className: 'mono' }}>{t.rr.toFixed(2)}</td>
                        <td style={{ padding: '12px 20px', className: 'mono' }}>{t.score}</td>
                        <td style={{ padding: '12px 20px', className: 'mono' }}>{t.kronos_confidence.toFixed(2)}</td>
                        <td style={{ padding: '12px 20px', color: C.muted, fontSize: '11px' }}>{new Date(t.entry_time * 1000).toLocaleDateString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {tab === 'Scanner' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '14px', color: C.muted }}>
                <span>Next scan in</span>
                <span className="mono" style={{ color: C.accent, fontWeight: 600 }}>{nextScan}s</span>
              </div>
              <div style={{ backgroundColor: C.card, borderRadius: '8px', border: `1px solid ${C.border}`, overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px', textAlign: 'left' }}>
                  <thead>
                    <tr style={{ backgroundColor: C.surface, color: C.muted, fontWeight: 600 }}>
                      <th style={{ padding: '12px 20px' }}>Symbol</th>
                      <th style={{ padding: '12px 20px' }}>Opportunity Score</th>
                      <th style={{ padding: '12px 20px' }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scanner.map((coin, i) => (
                      <tr key={i} style={{ borderBottom: `1px solid ${C.border}` }}>
                        <td style={{ padding: '12px 20px', fontWeight: 600 }}>{coin.symbol}</td>
                        <td style={{ padding: '12px 20px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <span className="mono">{coin.score.toFixed(1)}</span>
                            <div style={{ width: '100px', height: '4px', backgroundColor: C.dim, borderRadius: '2px' }}>
                              <div style={{ width: `${Math.min(100, coin.score)}%`, height: '100%', backgroundColor: C.accent, borderRadius: '2px' }}></div>
                            </div>
                          </div>
                        </td>
                        <td style={{ padding: '12px 20px' }}>
                          <span style={{ 
                            padding: '2px 6px', 
                            borderRadius: '4px', 
                            fontSize: '10px', 
                            fontWeight: 700, 
                            backgroundColor: coin.score > 50 ? C.accentDim : C.dim, 
                            color: coin.score > 50 ? C.accent : C.muted 
                          }}>
                            {coin.score > 70 ? 'ANALYZING' : coin.score > 40 ? 'WATCH' : 'LOW'}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {tab === 'Settings' && (
            <div style={{ maxWidth: '600px', margin: '0 auto', backgroundColor: C.card, padding: '30px', borderRadius: '12px', border: `1px solid ${C.border}` }}>
              <div style={{ fontSize: '20px', fontWeight: 700, marginBottom: '24px', color: C.accent }}>Bot Configuration</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                <div>
                  <label style={{ display: 'block', color: C.muted, fontSize: '12px', marginBottom: '8px' }}>MEXC API Key</label>
                  <input 
                    style={{ width: '100%', padding: '10px', backgroundColor: C.surface, border: `1px solid ${C.border}`, borderRadius: '4px', color: C.text, fontFamily: FONTS.mono }}
                    value={settings.MEXC_API_KEY} 
                    onChange={e => setSettings({...settings, MEXC_API_KEY: e.target.value})}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', color: C.muted, fontSize: '12px', marginBottom: '8px' }}>MEXC Secret Key</label>
                  <input 
                    type="password"
                    style={{ width: '100%', padding: '10px', backgroundColor: C.surface, border: `1px solid ${C.border}`, borderRadius: '4px', color: C.text, fontFamily: FONTS.mono }}
                    value={settings.MEXC_SECRET_KEY} 
                    onChange={e => setSettings({...settings, MEXC_SECRET_KEY: e.target.value})}
                  />
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 0' }}>
                  <label style={{ color: C.text, fontWeight: 600 }}>Paper Trading Mode</label>
                  <input 
                    type="checkbox" 
                    checked={settings.PAPER_TRADING} 
                    onChange={e => setSettings({...settings, PAPER_TRADING: e.target.checked})}
                    style={{ width: '20px', height: '20px' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', color: C.muted, fontSize: '12px', marginBottom: '8px' }}>Kronos Model Size</label>
                  <select 
                    style={{ width: '100%', padding: '10px', backgroundColor: C.surface, border: `1px solid ${C.border}`, borderRadius: '4px', color: C.text }}
                    value={settings.KRONOS_MODEL}
                    onChange={e => setSettings({...settings, KRONOS_MODEL: e.target.value})}
                  >
                    <option value="mini">Mini (Fastest)</option>
                    <option value="small">Small (Balanced)</option>
                    <option value="base">Base (Most Accurate)</option>
                  </select>
                </div>
                <button 
                  onClick={saveSettings}
                  style={{ 
                    padding: '12px', 
                    backgroundColor: C.accent, 
                    color: C.bg, 
                    border: 'none', 
                    borderRadius: '4px', 
                    fontWeight: 700, 
                    cursor: 'pointer', 
                    marginTop: '20px' 
                  }}
                >
                  Save Configuration
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default App;
