import { useState } from "react";
import { ACTIVE_MODEL_NAMES } from "../data/models";

type TimeRange = "24h" | "7d" | "30d";

interface TokenDataPoint {
  label: string;
  promptTokens: number;
  completionTokens: number;
}

// Token figures per model slot — indices map to ACTIVE_MODEL_NAMES order
const MODEL_TOKEN_DATA: Record<TimeRange, { promptTokens: number; completionTokens: number }[]> = {
  "24h": [
    { promptTokens: 620000, completionTokens: 380000 },
    { promptTokens: 480000, completionTokens: 290000 },
    { promptTokens: 210000, completionTokens: 120000 },
  ],
  "7d": [
    { promptTokens: 3950000, completionTokens: 2210000 },
    { promptTokens: 3120000, completionTokens: 1910000 },
    { promptTokens: 1410100, completionTokens: 820400 },
  ],
  "30d": [
    { promptTokens: 17200000, completionTokens: 9100000 },
    { promptTokens: 13900000, completionTokens: 8100000 },
    { promptTokens: 5880000, completionTokens: 3640000 },
  ],
};

function buildModelSplit(range: TimeRange): TokenDataPoint[] {
  return ACTIVE_MODEL_NAMES.map((name, i) => ({
    label: name,
    ...(MODEL_TOKEN_DATA[range][i] ?? { promptTokens: 0, completionTokens: 0 }),
  }));
}

const RANGE_DATA: Record<TimeRange, {
  summary: { prompt: number; completion: number; total: number; savingsPct: number };
  modelSplit: TokenDataPoint[];
  dailySplit: TokenDataPoint[];
}> = {
  "24h": {
    summary: { prompt: 1420500, completion: 820300, total: 2240800, savingsPct: 18 },
    modelSplit: buildModelSplit("24h"),
    dailySplit: [
      { label: "00:00-06:00", promptTokens: 320000, completionTokens: 180000 },
      { label: "06:00-12:00", promptTokens: 410000, completionTokens: 220000 },
      { label: "12:00-18:00", promptTokens: 490000, completionTokens: 290000 },
      { label: "18:00-24:00", promptTokens: 200500, completionTokens: 130300 },
    ],
  },
  "7d": {
    summary: { prompt: 9480100, completion: 5390400, total: 14870500, savingsPct: 22 },
    modelSplit: buildModelSplit("7d"),
    dailySplit: [
      { label: "Mon", promptTokens: 1210000, completionTokens: 710000 },
      { label: "Tue", promptTokens: 1430000, completionTokens: 820000 },
      { label: "Wed", promptTokens: 1550000, completionTokens: 910000 },
      { label: "Thu", promptTokens: 1390000, completionTokens: 790000 },
      { label: "Fri", promptTokens: 1610000, completionTokens: 930000 },
      { label: "Sat", promptTokens: 1100100, completionTokens: 610400 },
      { label: "Sun", promptTokens: 1190000, completionTokens: 620000 },
    ],
  },
  "30d": {
    summary: { prompt: 41080000, completion: 23140000, total: 64220000, savingsPct: 24 },
    modelSplit: buildModelSplit("30d"),
    dailySplit: [
      { label: "Week 1", promptTokens: 9200000, completionTokens: 5100000 },
      { label: "Week 2", promptTokens: 11100000, completionTokens: 6200000 },
      { label: "Week 3", promptTokens: 12100000, completionTokens: 6800000 },
      { label: "Week 4", promptTokens: 8680000, completionTokens: 5040000 },
    ],
  },
};

function formatNumber(num: number): string {
  if (num >= 1000000) {
    return (num / 1000000).toFixed(1) + "M";
  }
  if (num >= 1000) {
    return (num / 1000).toFixed(1) + "k";
  }
  return num.toString();
}

export default function TokenMetricsView() {
  const [range, setRange] = useState<TimeRange>("7d");
  const data = RANGE_DATA[range];

  const maxVal = Math.max(
    ...data.modelSplit.map((x) => x.promptTokens + x.completionTokens),
    ...data.dailySplit.map((x) => x.promptTokens + x.completionTokens)
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      {/* Header controls row */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 16 }}>
        <div>
          <h1>Token Consumption</h1>
          <p style={{ color: "var(--text-muted)", fontSize: 14.5, marginTop: -16, marginBottom: 0 }}>
            Visual analytics monitoring prompt, completion, and cached token throughput.
          </p>
        </div>

        <div style={{ display: "flex", background: "#ffffff", padding: 4, borderRadius: 8, border: "1px solid var(--border-color)", boxShadow: "var(--shadow-sm)" }}>
          {(["24h", "7d", "30d"] as TimeRange[]).map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              style={{
                border: "none",
                background: range === r ? "var(--primary-light)" : "transparent",
                color: range === r ? "var(--primary)" : "var(--text-muted)",
                padding: "6px 14px",
                borderRadius: 6,
                cursor: "pointer",
                fontWeight: 600,
                fontSize: 12.5,
                transition: "all 0.15s ease",
              }}
            >
              {r === "24h" ? "Last 24 Hours" : r === "7d" ? "Last 7 Days" : "Last 30 Days"}
            </button>
          ))}
        </div>
      </div>

      {/* Metrics Row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 20 }}>
        {/* Card 1 */}
        <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Total Token Volume
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, color: "var(--primary)" }}>{formatNumber(data.summary.total)}</div>
          <div style={{ fontSize: 12, color: "var(--text-light)" }}>Prompt + Completion</div>
        </div>

        {/* Card 2 */}
        <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Prompt Tokens
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, color: "var(--text-main)" }}>{formatNumber(data.summary.prompt)}</div>
          <div style={{ fontSize: 12, color: "var(--text-light)" }}>
            {((data.summary.prompt / data.summary.total) * 100).toFixed(0)}% of total volume
          </div>
        </div>

        {/* Card 3 */}
        <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Completion Tokens
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, color: "var(--text-main)" }}>{formatNumber(data.summary.completion)}</div>
          <div style={{ fontSize: 12, color: "var(--text-light)" }}>
            {((data.summary.completion / data.summary.total) * 100).toFixed(0)}% of total volume
          </div>
        </div>

        {/* Card 4 */}
        <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Cache Hit Savings
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, color: "var(--accent-green-text)" }}>{data.summary.savingsPct}%</div>
          <div style={{ fontSize: 12, color: "var(--text-light)" }}>Estimated costs avoided</div>
        </div>
      </div>

      {/* Charts Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 24 }}>
        {/* Model Split Chart */}
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <h2 style={{ margin: 0 }}>Usage by Model</h2>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {data.modelSplit.map((pt) => {
              const total = pt.promptTokens + pt.completionTokens;
              const promptPct = (pt.promptTokens / maxVal) * 100;
              const completionPct = (pt.completionTokens / maxVal) * 100;

              return (
                <div key={pt.label} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 13 }}>
                    <span style={{ fontWeight: 600, color: "var(--text-main)" }}>{pt.label}</span>
                    <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                      {formatNumber(total)} <span style={{ fontSize: 11, color: "var(--text-light)" }}>tokens</span>
                    </span>
                  </div>
                  {/* Bar graph */}
                  <div style={{ height: 16, width: "100%", background: "#f3f4f6", borderRadius: 8, display: "flex", overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${promptPct}%`,
                        background: "var(--primary)",
                        transition: "width 0.4s ease-out",
                      }}
                      title={`Prompt: ${formatNumber(pt.promptTokens)}`}
                    />
                    <div
                      style={{
                        width: `${completionPct}%`,
                        background: "#a78bfa",
                        transition: "width 0.4s ease-out",
                      }}
                      title={`Completion: ${formatNumber(pt.completionTokens)}`}
                    />
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ display: "flex", gap: 16, fontSize: 12, marginTop: 4 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ width: 10, height: 10, borderRadius: "50%", background: "var(--primary)" }} />
              <span style={{ color: "var(--text-muted)" }}>Prompt Tokens</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#a78bfa" }} />
              <span style={{ color: "var(--text-muted)" }}>Completion Tokens</span>
            </div>
          </div>
        </div>

        {/* DailySplit Timeline Chart */}
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <h2 style={{ margin: 0 }}>Consumption Timeline</h2>
          <div
            style={{
              height: 220,
              display: "flex",
              alignItems: "flex-end",
              justifyContent: "space-between",
              gap: 12,
              paddingBottom: 24,
              borderBottom: "1px solid var(--border-color)",
            }}
          >
            {data.dailySplit.map((pt, i) => {
              const total = pt.promptTokens + pt.completionTokens;
              const barHeightPct = (total / maxVal) * 90;

              return (
                <div
                  key={i}
                  style={{
                    flex: 1,
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    gap: 8,
                    height: "100%",
                    justifyContent: "flex-end",
                  }}
                >
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    {formatNumber(total)}
                  </div>
                  <div
                    style={{
                      width: "min(32px, 100%)",
                      height: `${barHeightPct}%`,
                      background: "linear-gradient(to top, var(--primary), #a78bfa)",
                      borderRadius: "6px 6px 0 0",
                      transition: "height 0.4s ease-out",
                      boxShadow: "0 2px 8px rgba(124, 58, 237, 0.15)",
                    }}
                    title={`Total: ${total}`}
                  />
                  <div
                    style={{
                      fontSize: 11.5,
                      color: "var(--text-light)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      maxWidth: "100%",
                    }}
                  >
                    {pt.label}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
