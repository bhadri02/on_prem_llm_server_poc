/**
 * AuditFilters
 *
 * Filter controls for the Audit Viewer.
 * Renders datetime pickers for from/to range, a layer dropdown,
 * and an outcome dropdown. Every change calls the corresponding
 * callback immediately so the parent can re-fetch.
 *
 * Requirements: 4.2, 4.4
 */

interface AuditFiltersProps {
  from: string;
  to: string;
  layer: string;
  outcome: string;
  onFromChange: (v: string) => void;
  onToChange: (v: string) => void;
  onLayerChange: (v: string) => void;
  onOutcomeChange: (v: string) => void;
}

const labelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  fontSize: 13,
  color: "#475569",
  fontWeight: 500,
};

const inputStyle: React.CSSProperties = {
  padding: "6px 10px",
  border: "1px solid #cbd5e1",
  borderRadius: 6,
  fontSize: 14,
  color: "#1e293b",
  background: "#ffffff",
  outline: "none",
};

export default function AuditFilters({
  from,
  to,
  layer,
  outcome,
  onFromChange,
  onToChange,
  onLayerChange,
  onOutcomeChange,
}: AuditFiltersProps) {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 16,
        alignItems: "flex-end",
        padding: "12px 16px",
        background: "#f1f5f9",
        borderRadius: 8,
        border: "1px solid #e2e8f0",
        marginBottom: 16,
      }}
    >
      {/* From datetime */}
      <label style={labelStyle}>
        From
        <input
          type="datetime-local"
          value={from}
          onChange={(e) => onFromChange(e.target.value)}
          style={inputStyle}
        />
      </label>

      {/* To datetime */}
      <label style={labelStyle}>
        To
        <input
          type="datetime-local"
          value={to}
          onChange={(e) => onToChange(e.target.value)}
          style={inputStyle}
        />
      </label>

      {/* Layer dropdown */}
      <label style={labelStyle}>
        Layer
        <select
          value={layer}
          onChange={(e) => onLayerChange(e.target.value)}
          style={inputStyle}
        >
          <option value="">All</option>
          <option value="api_gateway">api_gateway</option>
          <option value="security">security</option>
          <option value="router">router</option>
          <option value="cache">cache</option>
          <option value="inference">inference</option>
          <option value="agent">agent</option>
          <option value="governance">governance</option>
          <option value="platform">platform</option>
        </select>
      </label>

      {/* Outcome dropdown */}
      <label style={labelStyle}>
        Outcome
        <select
          value={outcome}
          onChange={(e) => onOutcomeChange(e.target.value)}
          style={inputStyle}
        >
          <option value="">All</option>
          <option value="pass">pass</option>
          <option value="block">block</option>
        </select>
      </label>
    </div>
  );
}
