"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

type Theme = "light" | "dark";

function readTheme(): Theme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.getAttribute("data-theme") === "light"
    ? "light"
    : "dark";
}

export function ThemeToggle({
  className,
  label = false,
}: {
  className?: string;
  label?: boolean;
}) {
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    const id = requestAnimationFrame(() => {
      const stored = localStorage.getItem("realmspace-theme");
      const next: Theme =
        stored === "light" || stored === "dark"
          ? stored
          : window.matchMedia("(prefers-color-scheme: light)").matches
            ? "light"
            : "dark";
      document.documentElement.setAttribute("data-theme", next);
      setTheme(next);
    });
    return () => cancelAnimationFrame(id);
  }, []);

  function toggle() {
    const next: Theme = readTheme() === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("realmspace-theme", next);
    setTheme(next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      className={cn(
        "min-h-11 rounded-[16px] flex items-center justify-center gap-2 px-3 text-text-secondary hover:text-text-primary hover:bg-bg-elevated transition-colors",
        className
      )}
      title={`Use ${theme === "light" ? "dark" : "light"} theme`}
      aria-label={`Use ${theme === "light" ? "dark" : "light"} theme`}
    >
      <span className="material-symbol material-symbol-sm">
        {theme === "light" ? "dark_mode" : "light_mode"}
      </span>
      {label && (
        <span className="text-sm font-semibold">
          {theme === "light" ? "Dark" : "Light"}
        </span>
      )}
    </button>
  );
}
