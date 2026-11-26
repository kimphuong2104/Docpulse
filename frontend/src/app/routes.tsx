import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { loadSession } from "../api/client";
import { WorkspaceShell } from "./WorkspaceShell";
import { Auth } from "../screens/Auth";
import { Editor } from "../screens/Editor";
import { Home } from "../screens/Home";
import { Landing } from "../screens/Landing";
import { Settings } from "../screens/Settings";
import { System } from "../screens/System";

function Guard() {
  if (!loadSession()) return <Navigate to="/login" replace />;
  return <Outlet />;
}

function Fallback() {
  const session = loadSession();
  return <Navigate to={session ? `/w/${session.workspace.slug}` : "/login"} replace />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Auth mode="login" />} />
      <Route path="/register" element={<Auth mode="register" />} />
      <Route element={<Guard />}>
        <Route path="/w/:workspaceId/settings" element={<Settings />} />
        <Route path="/w/:workspaceId" element={<WorkspaceShell />}>
          <Route index element={<Home />} />
          <Route path="p/:pageId" element={<Editor />} />
          <Route path="system" element={<System />} />
          <Route path="search" element={<Home />} />
        </Route>
      </Route>
      <Route path="*" element={<Fallback />} />
    </Routes>
  );
}
