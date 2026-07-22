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
      className="card"
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 20,
        alignItems: "flex-end",
        padding: "16px 20px",
        marginBottom: 24,
      }}
    >
      {/* From datetime */}
      <div className="form-group">
        <label htmlFor="filter-from" className="form-label">
          From
        </label>
        <input
          id="filter-from"
          type="datetime-local"
          value={from}
          onChange={(e) => onFromChange(e.target.value)}
          className="form-input"
        />
      </div>

      {/* To datetime */}
      <div className="form-group">
        <label htmlFor="filter-to" className="form-label">
          To
        </label>
        <input
          id="filter-to"
          type="datetime-local"
          value={to}
          onChange={(e) => onToChange(e.target.value)}
          className="form-input"
        />
      </div>

      {/* Layer dropdown */}
      <div className="form-group">
        <label htmlFor="filter-layer" className="form-label">
          Layer
        </label>
        <select
          id="filter-layer"
          value={layer}
          onChange={(e) => onLayerChange(e.target.value)}
          className="form-select"
          style={{ minWidth: 160 }}
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
      </div>

      {/* Outcome dropdown */}
      <div className="form-group">
        <label htmlFor="filter-outcome" className="form-label">
          Outcome
        </label>
        <select
          id="filter-outcome"
          value={outcome}
          onChange={(e) => onOutcomeChange(e.target.value)}
          className="form-select"
          style={{ minWidth: 140 }}
        >
          <option value="">All</option>
          <option value="pass">pass</option>
          <option value="block">block</option>
        </select>
      </div>
    </div>
  );
}
