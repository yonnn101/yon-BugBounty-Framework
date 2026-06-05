import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { adminPing } from "../api/client";
import { useAuth } from "../context/AuthContext";

export function Layout() {
  const { user, logout } = useAuth();
  const [adminMsg, setAdminMsg] = useState<string | null>(null);
  const navCls = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-3 py-2 text-sm font-medium transition ${
      isActive ? "bg-surface-700 text-accent" : "text-slate-400 hover:bg-surface-700 hover:text-slate-200"
    }`;

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-surface-600 bg-surface-800/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <Link to="/dashboard" className="font-display text-lg font-bold tracking-tight text-white">
            yonnn
          </Link>
          <nav className="flex items-center gap-1">
            <NavLink to="/dashboard" className={navCls}>
              Dashboard
            </NavLink>
            <NavLink to="/programs" className={navCls}>
              Programs
            </NavLink>
          </nav>
          <div className="flex items-center gap-3 text-sm">
            <span className="hidden sm:inline text-slate-500 truncate max-w-[12rem]" title={user?.email}>
              {user?.email}
            </span>
            {user?.is_superuser && (
              <button
                type="button"
                title="Call GET /api/admin/ping"
                onClick={async () => {
                  setAdminMsg(null);
                  try {
                    const r = await adminPing();
                    setAdminMsg(r.status);
                  } catch (e) {
                    setAdminMsg(e instanceof Error ? e.message : "failed");
                  }
                }}
                className="rounded bg-accent/15 px-2 py-0.5 text-xs text-accent hover:bg-accent/25"
              >
                admin ping
              </button>
            )}
            {adminMsg && <span className="text-xs text-slate-500">{adminMsg}</span>}
            <button
              type="button"
              onClick={() => void logout()}
              className="rounded-md border border-surface-600 px-3 py-1.5 text-slate-300 hover:border-slate-500 hover:text-white"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
