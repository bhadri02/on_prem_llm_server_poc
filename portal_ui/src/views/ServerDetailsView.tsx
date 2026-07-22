import { useState, useEffect } from "react";
import indiaMapSvg from "../assets/india_map.svg?raw";

export default function ServerDetailsView() {
  const [logs, setLogs] = useState<string[]>([
    "CONNECTING NODE_HSR_01... SUCCESS",
    "SYS.LOC: HSR_DC_04 initialized",
    "NET.RT: ACTIVE - 10G OPTIC",
  ]);

  useEffect(() => {
    const messages = [
      "SYSTEM TEMP: 41°C (Normal)",
      "FAN SPEED: 3100 RPM",
      "ZFS POOL STATUS: HEALTHY (RAID 10)",
      "DATABASE SYNC: OK (sales.db)",
      "OS UPDATE: ALMALINUX 9.4 UP-TO-DATE",
      "LATENCY CHECK: 1.15ms (LAN)",
      "SECURITY COMPLIANCE: PASSED",
      "INTRANET GATEWAY: SECURED",
      "BACKUP DAEMON: STANDBY",
    ];

    const interval = setInterval(() => {
      setLogs((prev) => {
        const next = [...prev, messages[Math.floor(Math.random() * messages.length)]];
        if (next.length > 5) {
          next.shift();
        }
        return next;
      });
    }, 2500);

    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      <style>{`
        @keyframes radar-glow-pulse {
          0% { transform: scale(0.95); opacity: 0.25; }
          50% { transform: scale(1.05); opacity: 0.45; }
          100% { transform: scale(0.95); opacity: 0.25; }
        }
        @keyframes ping-ring {
          0% { r: 4px; opacity: 1; }
          100% { r: 35px; opacity: 0; }
        }
        @keyframes radar-rotate {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        .radar-glow {
          transform-origin: 145px 315px;
          animation: radar-glow-pulse 3s ease-in-out infinite;
        }
        .radar-ping-ring {
          transform-origin: 145px 315px;
          animation: ping-ring 2.5s cubic-bezier(0.215, 0.61, 0.355, 1) infinite;
        }
        .radar-ping-ring-2 {
          transform-origin: 145px 315px;
          animation: ping-ring 2.5s cubic-bezier(0.215, 0.61, 0.355, 1) infinite;
          animation-delay: 1.25s;
        }
        #radar-sweep-hand {
          transform-origin: 145px 315px;
          animation: radar-rotate 8s linear infinite;
        }
        .aux-ping {
          animation: ping-ring 3s ease-out infinite;
        }
        .config-table {
          width: 100%;
          border-collapse: collapse;
        }
        .config-table td {
          padding: 14px 16px;
          border-bottom: 1px solid var(--border-color);
          font-size: 14px;
        }
        .config-table tr:last-child td {
          border-bottom: none;
        }
        .config-label {
          font-weight: 600;
          color: var(--text-main);
          width: 35%;
        }
        .config-value {
          color: var(--text-muted);
        }
        .code-style {
          font-family: var(--font-mono);
          font-size: 12.5px;
          background-color: var(--primary-light);
          color: var(--primary-text);
          padding: 2px 6px;
          border-radius: 4px;
          border: 1px solid var(--primary-border);
        }
        /* Custom map container sizing & styles */
        .svg-container-map svg {
          width: 100%;
          height: 100%;
          display: block;
          filter: saturate(1.3);
        }
        .svg-container-map svg .india-outline path {
          fill: rgba(167, 139, 250, 0.18) !important;
          stroke: rgba(109, 40, 217, 0.55) !important;
          stroke-width: 1.1px !important;
          transition: fill 0.2s ease;
        }
        .svg-container-map svg .india-outline path:hover {
          fill: rgba(167, 139, 250, 0.38) !important;
        }
      `}</style>

      <div>
        <h1 style={{ margin: 0 }}>On-Premises Server Details</h1>
        <p style={{ color: "var(--text-muted)", fontSize: 14.5, marginTop: 4, marginBottom: 0 }}>
          Private cloud compute specifications for GWC Private AI's local intelligence pipeline. Runs strictly inside the intranet for low-latency database queries and file access.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(400px, 1fr))", gap: 24 }}>
        {/* Left: Configuration specs */}
        <div className="card" style={{ padding: 24, display: "flex", flexDirection: "column", gap: 16 }}>
          <h2 style={{ margin: 0, fontSize: 18, color: "var(--primary)" }}>Compute & Node Details</h2>
          
          <table className="config-table">
            <tbody>
              <tr>
                <td className="config-label">Node Hostname</td>
                <td className="config-value"><span className="code-style">gwc-onprem-node-01.hosur.internal</span></td>
              </tr>
              <tr>
                <td className="config-label">IP Address</td>
                <td className="config-value"><span className="code-style">10.142.48.12</span></td>
              </tr>
              <tr>
                <td className="config-label">Operating System</td>
                <td className="config-value">AlmaLinux OS 9.4 (Stone Smoked)</td>
              </tr>
              <tr>
                <td className="config-label">Compute CPU</td>
                <td className="config-value">2x Intel Xeon Scalable Gold 6430 (64 Cores, 128 Threads)</td>
              </tr>
              <tr>
                <td className="config-label">System RAM</td>
                <td className="config-value">512 GB DDR5 ECC</td>
              </tr>
              <tr>
                <td className="config-label">NVMe Storage</td>
                <td className="config-value">4x 3.84TB NVMe SSD U.2 (RAID 10, ZFS pool)</td>
              </tr>
              <tr>
                <td className="config-label">Local Database</td>
                <td className="config-value"><span className="code-style">SQLite v3.45</span></td>
              </tr>
              <tr>
                <td className="config-label">Datacenter Location</td>
                <td className="config-value">Hosur Factory Datacenter, Server Rack B-04</td>
              </tr>
              <tr>
                <td className="config-label">Intranet Latency</td>
                <td className="config-value"><span className="code-style">~1.2 ms (LAN switch connection)</span></td>
              </tr>
              <tr>
                <td className="config-label">Node System Status</td>
                <td className="config-value" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      backgroundColor: "var(--accent-green-text)",
                      boxShadow: "0 0 8px var(--accent-green-text)",
                    }}
                  />
                  <span style={{ color: "var(--accent-green-text)", fontWeight: 600 }}>Active / Operational</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        {/* Right: Location India Map */}
        <div className="card" style={{ padding: 24, display: "flex", flexDirection: "column", gap: 20 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 18, color: "var(--primary)" }}>On-Premises Location & Telemetry</h2>
            <p style={{ color: "var(--text-light)", fontSize: 13, marginTop: 4, marginBottom: 0 }}>
              Hosur Node coordinates: <span className="code-style">12.7365° N, 77.8326° E</span>
            </p>
          </div>

          {/* Light-Theme map container — viewBox crop is baked into the SVG file itself */}
          <div
            className="svg-container-map"
            style={{
              width: "100%",
              height: 400,
              backgroundColor: "#f8f7ff",
              borderRadius: 8,
              border: "1px solid rgba(109,40,217,0.15)",
              position: "relative",
              overflow: "hidden",
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              ["--accent" as any]: "rgba(109, 40, 217, 0.55)",
              ["--border" as any]: "rgba(109, 40, 217, 0.4)",
            }}
            dangerouslySetInnerHTML={{ __html: indiaMapSvg }}
          />

          {/* Telemetry log terminal (Light violet style) */}
          <div
            style={{
              backgroundColor: "var(--primary-light)",
              borderRadius: 8,
              border: "1px solid var(--primary-border)",
              padding: "12px 16px",
              display: "flex",
              flexDirection: "column",
              gap: 6,
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--primary-text)",
              minHeight: 106,
            }}
          >
            {logs.map((log, index) => (
              <div key={index} style={{ display: "flex", gap: 10 }}>
                <span style={{ color: "var(--primary)", fontWeight: "bold" }}>&gt;</span>
                <span>{log}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
