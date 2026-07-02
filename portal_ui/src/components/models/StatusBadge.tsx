/**
 * StatusBadge
 *
 * Renders a coloured pill badge for a model's lifecycle status.
 *
 * - active  → green
 * - staging → yellow
 * - retired → grey
 *
 * Requirements: 6.3
 */

import type { ModelRecord } from "../../types";

interface StatusBadgeProps {
  status: ModelRecord["status"];
}

const STATUS_STYLES: Record<
  ModelRecord["status"],
  { background: string; color: string; label: string }
> = {
  active: { background: "#dcfce7", color: "#15803d", label: "Active" },
  staging: { background: "#fef9c3", color: "#854d0e", label: "Staging" },
  retired: { background: "#f1f5f9", color: "#64748b", label: "Retired" },
};

export default function StatusBadge({ status }: StatusBadgeProps) {
  const { background, color, label } = STATUS_STYLES[status];

  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 10px",
        borderRadius: 12,
        fontWeight: 600,
        fontSize: 12,
        background,
        color,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}
