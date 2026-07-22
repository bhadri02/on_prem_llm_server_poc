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
  { className: string; background: string; color: string; label: string }
> = {
  active: { className: "badge badge-green", background: "rgb(220, 252, 231)", color: "rgb(21, 128, 61)", label: "Active" },
  staging: { className: "badge badge-yellow", background: "rgb(254, 249, 195)", color: "rgb(133, 77, 14)", label: "Staging" },
  retired: { className: "badge badge-gray", background: "rgb(241, 245, 249)", color: "rgb(100, 116, 139)", label: "Retired" },
  pending: { className: "badge badge-yellow", background: "rgba(245, 158, 11, 0.15)", color: "rgb(180, 83, 9)", label: "Pending" },
};

export default function StatusBadge({ status }: StatusBadgeProps) {
  const { className, background, color, label } = STATUS_STYLES[status];

  return (
    <span className={className} style={{ background, color }}>
      {label}
    </span>
  );
}
