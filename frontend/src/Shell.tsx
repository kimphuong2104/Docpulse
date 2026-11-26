import { useState } from "react";
import App from "./App";
import { Workspace } from "./workspace/Workspace";

export default function Shell() {
  const [view, setView] = useState<"workspace" | "library">("workspace");
  if (view === "library") return <App onOpenWorkspace={() => setView("workspace")} />;
  return <Workspace onOpenLibrary={() => setView("library")} />;
}
