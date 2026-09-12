import type { Instance } from "./types";

const STATUS_CLASS: Record<string, string> = {
  active: "ok",
  missing: "warn",
  stale: "warn",
  unmanaged: "muted",
};

export function InstanceList({
  instances,
  onSelect,
}: {
  instances: Instance[];
  onSelect: (id: string) => void;
}) {
  return (
    <table className="instances">
      <thead>
        <tr>
          <th>Name</th>
          <th>Status</th>
          <th>Engine</th>
          <th>Tech stack</th>
          <th>Last scan</th>
          <th>Path</th>
        </tr>
      </thead>
      <tbody>
        {instances.map((i) => (
          <tr key={i.id} className="row" onClick={() => onSelect(i.id)}>
            <td>{i.name || i.path}</td>
            <td>
              <span className={`badge ${STATUS_CLASS[i.status] || ""}`}>{i.status}</span>
            </td>
            <td>
              <code>{i.engine_version}</code>
            </td>
            <td>{(i.tech_stack || []).join(", ") || "—"}</td>
            <td>
              {i.last_scanned_at ? new Date(i.last_scanned_at).toLocaleString() : "never"}
            </td>
            <td className="path">{i.path}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
