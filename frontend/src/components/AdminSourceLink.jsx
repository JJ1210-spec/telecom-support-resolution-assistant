import { Link } from "react-router-dom";
import { Cite } from "./ui";

export function sourcePath(id) {
  return `/console/sources/${encodeURIComponent(id)}`;
}

export function AdminSourceLink({ id, children }) {
  return (
    <Link className={`admin-source-link${children ? " text" : ""}`} to={sourcePath(id)} title={`Open full source ${id}`}>
      {children ?? <Cite id={id} />}
    </Link>
  );
}
